"""Season scoring pipeline and board data (SCORE-01..06, BOARD-01..04).

    fc season score --season 2026-us-midterms [--truth FILE] [--root .] [--out eval/out/<season>]

Inputs
  model-output/<entry>/<season>-<date>.csv      daily forecasts (Tier S from the runner,
                                                Tier A from reveals)
  model-metadata/<entry>.yml                    tier, freeze time (for admission)
  rounds/<season>/<date>/manifest.json          T_open per day
  target-data/truth/<season>/truth.csv          question_id, y, status, vintage
                                                (provisional | final | void)
Outputs (all CSV/JSON, consumed by the static board)
  forecasts.csv       every row, with model_id, day, tier
  scores.csv          per (model_id, day, question_id): score, imputed flag, truth status
  entry_days.csv      per (model_id, day): mean score, n, parse coverage
  relative_skill.csv  per tier: rel skill vs fc-baseline, 95 % CI by day block, N days
  trajectories.csv    per (model_id, day): P(D House), P(D Senate), seat medians
  calibration.csv     per (model_id, tier): reliability bins, Brier, interval coverage
  board.json          everything the HTML needs, including the provisional flag
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from . import baselines as B
from . import questions as Q
from .eval import bootstrap_relative_skill, ensemble_mean_probs, score_frame

TIERS = ("S", "A", "B", "H")
BASELINE = "fc-baseline"
BASELINE_IDS = {"fc-baseline": "H", "fc-uniform": "H", "fc-market": "H", "hub-ensemble": "H"}


def load_entries(root: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for p in sorted((root / "model-metadata").glob("*.yml")):
        doc = yaml.safe_load(p.read_text()) or {}
        if "model_id" in doc:
            out[doc["model_id"]] = doc
    return out


def freeze_time(entry: dict[str, Any], root: Path) -> datetime | None:
    """Anchor time if recorded on the sidecar's root, else the hub receipt time, else None.
    (Receipt time is hub-asserted; the board labels it so until the anchor lands.)"""
    sc = (entry.get("freeze") or {}).get("sidecar")
    if not sc:
        return None
    p = root / sc
    if not p.is_file():
        return None
    rec = json.loads(p.read_text())
    t = (rec.get("hub_receipt") or {}).get("server_time")
    return datetime.fromisoformat(t) if t else None


def load_days(root: Path, season: str) -> dict[str, dict[str, Any]]:
    days = {}
    for p in sorted((root / "rounds" / season).glob("*/manifest.json")):
        m = json.loads(p.read_text())
        if m.get("kind") == "daily":
            days[m["day"]] = m
    return days


def load_forecasts(root: Path, season: str, days: dict[str, Any],
                   entries: dict[str, Any]) -> pd.DataFrame:
    frames = []
    for d, m in days.items():
        for p in sorted((root / "model-output").glob(f"*/{season}-{d}.csv")):
            model_id = p.parent.name
            df = pd.read_csv(p)
            if "round_id" in df.columns and (df["round_id"] != m["round_id"]).any():
                continue
            df["round_id"] = m["round_id"]
            df.insert(0, "model_id", model_id)
            df["day"] = d
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["model_id", "round_id", "question_id", "output_type",
                                     "output_type_id", "value", "day"])
    out = pd.concat(frames, ignore_index=True)
    out["tier"] = out["model_id"].map(lambda m: entries.get(m, {}).get("tier", BASELINE_IDS.get(m)))
    return out


def admitted(forecasts: pd.DataFrame, entries: dict[str, Any], days: dict[str, Any],
             root: Path) -> pd.DataFrame:
    """FREEZE-05: drop (entry, day) pairs where the freeze did not precede T_open."""
    keep = []
    cache: dict[str, datetime | None] = {}
    for (m, d), g in forecasts.groupby(["model_id", "day"]):
        if m in BASELINE_IDS:
            keep.append(g)
            continue
        if m not in cache:
            cache[m] = freeze_time(entries.get(m, {}), root) if m in entries else None
        ft = cache[m]
        t_open = datetime.fromisoformat(days[d]["T_open"])
        if ft is not None and ft < t_open:
            keep.append(g)
    return pd.concat(keep, ignore_index=True) if keep else forecasts.iloc[0:0]


def add_baselines(forecasts: pd.DataFrame, questions: list[dict], days: dict[str, Any],
                  packets_root: Path | None = None) -> pd.DataFrame:
    parts = [forecasts]
    for d, m in days.items():
        b = B.season_baselines(questions, m["round_id"])
        b["day"] = d
        b["tier"] = "H"
        parts.append(b)
        mk = market_baseline(questions, m["round_id"], packets_root / d if packets_root else None)
        if mk is not None:
            mk["day"] = d
            mk["tier"] = "H"
            parts.append(mk)
    all_ = pd.concat(parts, ignore_index=True)
    # Ensemble over non-baseline entries only, per day.
    ens = []
    for d, g in all_.groupby("day"):
        real = g[~g["model_id"].isin(BASELINE_IDS)]
        if real["model_id"].nunique() >= 2:
            e = ensemble_mean_probs(real[["round_id", "question_id", "output_type",
                                          "output_type_id", "value"]])
            e["day"] = d
            e["tier"] = "H"
            ens.append(e)
    if ens:
        all_ = pd.concat([all_, *ens], ignore_index=True)
    return all_


def market_baseline(questions: list[dict], round_id: str,
                    packet_dir: Path | None) -> pd.DataFrame | None:
    """`fc-market`: prices from the day's packet for mapped questions (binary/categorical only)."""
    if packet_dir is None or not (packet_dir / "packet.json").is_file():
        return None
    pk = json.loads((packet_dir / "packet.json").read_text())
    sec = next((s for s in pk["sections"] if s["name"] == "prediction_markets"), None)
    if not sec or sec["status"] != "ok":
        return None
    by_id = {q["question_id"]: q for q in questions}
    rows = []
    for qid, e in sec["data"].items():
        q = by_id.get(qid)
        if q is None:
            continue
        pm = e.get("polymarket") or {}
        ks = (e.get("kalshi") or {}).get("yes_last")
        if q["output_type"] == "binary":
            p = pm.get("Democratic", pm.get("Yes", ks))
            if p is None:
                continue
            rows.append({"model_id": "fc-market", "round_id": round_id, "question_id": qid,
                         "output_type": "binary", "output_type_id": "p", "value": float(p)})
        elif q["output_type"] == "categorical" and pm:
            pd_ = pm.get("Democratic", pm.get("Democrat"))
            pr_ = pm.get("Republican")
            if pd_ is None or pr_ is None:
                continue
            other = max(0.0, 1 - float(pd_) - float(pr_))
            tot = float(pd_) + float(pr_) + other
            for c, v in (("D", pd_), ("R", pr_), ("Other", other)):
                rows.append({"model_id": "fc-market", "round_id": round_id, "question_id": qid,
                             "output_type": "categorical", "output_type_id": c,
                             "value": float(v) / tot})
    return pd.DataFrame(rows) if rows else None


def impute_missing(scores: pd.DataFrame, forecasts: pd.DataFrame, questions: list[dict],
                   truth: pd.DataFrame) -> pd.DataFrame:
    """SCORE-05: for every (entry, day) with a resolved core question it did not answer,
    impute the worse of the baseline score and the 75th percentile of submitted scores."""
    core = {q["question_id"] for q in questions if q.get("core", True)}
    resolved = set(truth[truth["status"] != "void"]["question_id"])
    out = scores.copy()
    out["imputed"] = False
    extra = []
    for (m, d), g in forecasts.groupby(["model_id", "day"]):
        answered = set(scores[(scores.model_id == m) & (scores.day == d)]["question_id"])
        for qid in sorted((core & resolved) - answered):
            day_scores = scores[(scores.day == d) & (scores.question_id == qid)]
            if day_scores.empty:
                continue
            base = day_scores[day_scores.model_id == BASELINE]["score"]
            q75 = float(day_scores["score"].quantile(0.75))
            val = max(q75, float(base.iloc[0])) if len(base) else q75
            extra.append({"model_id": m, "day": d, "round_id": g["round_id"].iloc[0],
                          "question_id": qid, "output_type": day_scores["output_type"].iloc[0],
                          "score": val, "imputed": True})
    if extra:
        out = pd.concat([out, pd.DataFrame(extra)], ignore_index=True)
    return out


def trajectories(forecasts: pd.DataFrame) -> pd.DataFrame:
    rows = []
    def val(g, qid, tid):
        s = g[(g.question_id == qid) & (g.output_type_id.astype(str) == tid)]["value"]
        return float(s.iloc[0]) if len(s) else np.nan

    for (m, d), g in forecasts.groupby(["model_id", "day"]):
        rows.append({"model_id": m, "day": d,
                     "p_house_d": val(g, "control-house", "p"),
                     "p_senate_d": val(g, "control-senate", "p"),
                     "seats_house_d_median": val(g, "seats-house-d", "0.5"),
                     "seats_senate_dcaucus_median": val(g, "seats-senate-dcaucus", "0.5")})
    return pd.DataFrame(rows)


def calibration(forecasts: pd.DataFrame, truth: pd.DataFrame, bins: int = 5) -> pd.DataFrame:
    """Reliability over all probability statements (binary p, categorical per-category)
    plus 50/95 % interval coverage for quantiles."""
    t = truth[truth["status"] != "void"].set_index("question_id")["y"]
    rows = []
    for m, g in forecasts.groupby("model_id"):
        probs = g[g.output_type.isin(["binary", "categorical"]) & g.question_id.isin(t.index)]
        p = probs["value"].astype(float).to_numpy()
        y = np.array([
            (int(float(t[q])) if ot == "binary" else int(str(t[q]) == str(tid)))
            for q, ot, tid in zip(probs.question_id, probs.output_type, probs.output_type_id)
        ], dtype=float) if len(probs) else np.array([])
        rec: dict[str, Any] = {"model_id": m, "n_probs": len(p)}
        if len(p):
            rec["brier"] = float(np.mean((p - y) ** 2))
            edges = np.linspace(0, 1, bins + 1)
            for i in range(bins):
                mask = (p >= edges[i]) & (p < edges[i + 1] if i < bins - 1 else p <= edges[i + 1])
                rec[f"bin{i}_n"] = int(mask.sum())
                rec[f"bin{i}_mean_p"] = float(p[mask].mean()) if mask.any() else np.nan
                rec[f"bin{i}_freq"] = float(y[mask].mean()) if mask.any() else np.nan
        qs = g[(g.output_type == "quantile") & g.question_id.isin(t.index)]
        cov50, cov95, n = [], [], 0
        for (q, _), gg in qs.groupby(["question_id", "day"]):
            v = gg.set_index(gg.output_type_id.astype(float))["value"]
            yv = float(t[q])
            if 0.25 in v.index and 0.75 in v.index:
                cov50.append(v[0.25] <= yv <= v[0.75])
            if 0.025 in v.index and 0.975 in v.index:
                cov95.append(v[0.025] <= yv <= v[0.975])
            n += 1
        rec.update(n_quantile=n, coverage50=float(np.mean(cov50)) if cov50 else np.nan,
                   coverage95=float(np.mean(cov95)) if cov95 else np.nan)
        rows.append(rec)
    return pd.DataFrame(rows)


def run(root: Path, season: str, *, truth_path: Path | None = None, out_dir: Path | None = None,
        n_boot: int = 200, min_days: int = 7, min_shared: int = 20) -> dict[str, Any]:
    questions = Q.load_questions(root / "questions" / season / "questions.json")
    entries = load_entries(root)
    days = load_days(root, season)
    fc = load_forecasts(root, season, days, entries)
    fc = admitted(fc, entries, days, root)
    fc = add_baselines(fc, questions, days, root / "target-data" / "packets" / season)
    truth_path = truth_path or root / "target-data" / "truth" / season / "truth.csv"
    truth = pd.read_csv(truth_path) if truth_path.is_file() else pd.DataFrame(
        columns=["question_id", "y", "status", "vintage"])
    truth = truth[truth["status"] != "void"] if len(truth) else truth
    out_dir = out_dir or root / "eval" / "out" / season
    out_dir.mkdir(parents=True, exist_ok=True)

    sc = score_frame(fc, truth) if len(truth) else pd.DataFrame(
        columns=["round_id", "model_id", "question_id", "output_type", "score"])
    sc = sc.merge(fc[["round_id", "day"]].drop_duplicates(), on="round_id", how="left")
    sc = impute_missing(sc, fc, questions, truth) if len(sc) else sc.assign(imputed=False)
    sc["tier"] = sc["model_id"].map(lambda m: entries.get(m, {}).get("tier", BASELINE_IDS.get(m)))

    rel = []
    for tier in TIERS:
        sub = sc[(sc.tier == tier) | (sc.model_id == BASELINE)] if tier != "H" else sc[sc.tier == "H"]
        if sub.empty or sub.model_id.nunique() < 2:
            continue
        keyed = sub.copy()
        keyed["question_id"] = keyed["day"] + "/" + keyed["question_id"]
        block = pd.Series({q: q.split("/")[0] for q in keyed.question_id.unique()})
        try:
            rs = bootstrap_relative_skill(keyed, BASELINE, n_boot=n_boot, block=block,
                                          min_shared=min_shared)
        except ValueError:
            continue
        rs["tier"] = tier
        rs["n_days"] = keyed.groupby("model_id")["day"].nunique().reindex(rs.index)
        rs["provisional"] = rs["n_days"] < min_days
        rel.append(rs.reset_index().rename(columns={"index": "model_id"}))
    rel_df = pd.concat(rel, ignore_index=True) if rel else pd.DataFrame(
        columns=["model_id", "rel_skill", "lo95", "hi95", "n_questions", "tier", "n_days",
                 "provisional"])

    entry_days = (sc.groupby(["model_id", "day"]).agg(mean_score=("score", "mean"),
                                                      n=("score", "size"),
                                                      imputed=("imputed", "sum")).reset_index()
                  if len(sc) else pd.DataFrame(columns=["model_id", "day", "mean_score", "n",
                                                        "imputed"]))
    traj = trajectories(fc)
    cal = calibration(fc, truth) if len(truth) else pd.DataFrame(columns=["model_id"])

    fc.to_csv(out_dir / "forecasts.csv", index=False)
    sc.to_csv(out_dir / "scores.csv", index=False)
    entry_days.to_csv(out_dir / "entry_days.csv", index=False)
    rel_df.to_csv(out_dir / "relative_skill.csv", index=False)
    traj.to_csv(out_dir / "trajectories.csv", index=False)
    cal.to_csv(out_dir / "calibration.csv", index=False)
    board = {
        "season": season, "days": sorted(days), "n_questions": len(questions),
        "n_resolved": int(truth["question_id"].nunique()) if len(truth) else 0,
        "entries": {m: {"tier": entries.get(m, {}).get("tier", BASELINE_IDS.get(m)),
                        "days": sorted(fc[fc.model_id == m]["day"].unique().tolist())}
                    for m in sorted(fc["model_id"].unique())},
        "relative_skill": json.loads(rel_df.to_json(orient="records")),
        "trajectories": json.loads(traj.to_json(orient="records")),
        "calibration": json.loads(cal.to_json(orient="records")),
        "min_days_for_headline": min_days,
    }
    (out_dir / "board.json").write_text(json.dumps(board, indent=1) + "\n")
    return board
