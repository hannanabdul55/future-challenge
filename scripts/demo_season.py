"""End-to-end synthetic season in a temp directory (or --root DIR): questions → season →
three daily rounds with offline packets → three frozen entries with synthetic forecasts →
a synthetic truth file → `fc season score` → the static board.

    uv run python scripts/demo_season.py [--root DIR] [--days 3]

Proves the loop without a GPU or the network. Entries' forecasts are drawn from a noisy
version of the persistence baseline so the board has something to rank.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import yaml

from fc import baselines as B
from fc import commit as C
from fc import freeze as F
from fc import model_identity as M
from fc import packet as P
from fc import questions as Q
from fc import season as S
from fc import season_board, season_eval
from fc import service as SV

HERE = Path(__file__).resolve().parents[1]


class FakeApi:
    def __init__(self, name):
        self.name = name

    def model_info(self, repo_id, *, revision=None, files_metadata=False):
        sha = C.sha256(self.name.encode()).hex()
        sib = SimpleNamespace(rfilename="m.gguf", size=4_000_000_000, blob_id="b" * 40,
                              lfs={"sha256": sha, "size": 4_000_000_000, "pointer_size": 134})
        return SimpleNamespace(sha=sha[:40], siblings=[sib], card_data={"license": "apache-2.0"},
                               gated=False)

    def list_repo_commits(self, repo_id, *, revision=None):
        return []


def main(root: Path, n_days: int) -> None:
    season = Q.SEASON
    qs = Q.generate_midterms(HERE / "questions" / season / "races.csv")
    qpath = root / "questions" / season / "questions.json"
    Q.write_question_set(qpath, qs, season=season)
    first = date(2026, 10, 26)
    S.init_season(root, season=season, questions_path=qpath, first_day=first,
                  last_day=first + timedelta(days=n_days - 1), last_admission="2026-11-03T12:00:00Z")
    (root / "fc" / "prompts").mkdir(parents=True, exist_ok=True)
    template = HERE / "fc" / "prompts" / "forecaster-v1.md"

    # Hub + three frozen entries, all frozen before day 1 opens.
    hub_priv, _hub_pub = C.generate_keypair()
    hub_sk = C.load_private(hub_priv)
    freeze_dir = root / "rounds" / season / "season"
    rng = np.random.default_rng(7)
    entries = {"acme-small": 0.05, "acme-sharp": 0.02, "zed-noisy": 0.15}
    for i, (mid, _) in enumerate(entries.items()):
        team_priv, team_pub = C.generate_keypair()
        ident = M.resolve(f"org/{mid}", "main", api=FakeApi(mid), prompt_template=template.read_bytes())
        meta = root / "model-metadata" / f"{mid}.yml"
        F.write_entry_yaml(meta, model_id=mid, team=mid.split("-")[0].title(), tier="S",
                           identity=ident, public_key_pem=team_pub.decode())
        sidecar = root / "sidecar" / mid / f"{season}-season.json"
        req, nonce = F.commit_identity(meta, season_round_id=f"{season}-season",
                                       team_sk=C.load_private(team_priv), sidecar_path=sidecar)
        receipt, outcome = SV.submit_commitment(req, C.load_public(team_pub), hub_sk, freeze_dir,
                                                now=datetime(2026, 10, 25, 12, i, tzinfo=UTC))
        assert outcome == "accepted"
        doc = yaml.safe_load(meta.read_text())
        doc["freeze"]["sidecar"] = str(sidecar.relative_to(root))
        meta.write_text(yaml.safe_dump(doc, sort_keys=False))
        assert receipt is not None
        root_doc = json.loads((freeze_dir / "roots" / f"{i}.json").read_text())
        # Proof omitted in the demo: the board reads receipt time; `fc verify` needs the proof.
        C.write_sidecar(sidecar, receipt, [], root_doc["root"],
                        team_signature=req.team_signature, nonce_hash=C.nonce_hash(nonce),
                        window_index=i)

    # Daily rounds with offline packets and synthetic forecasts.
    def http(url, params=None):
        raise TimeoutError("offline demo")

    for k in range(n_days):
        d = first + timedelta(days=k)
        pdir = root / "target-data" / "packets" / season / d.isoformat()
        srcs = P.default_sources({}, qs)
        for s_ in srcs:
            if hasattr(s_, "pause_s"):
                s_.pause_s = 0
        P.build(d, pdir, sources=srcs, http=http, season=season,
                now=datetime.combine(d, datetime.min.time(), tzinfo=UTC))
        m = S.open_round(root, season=season, d=d, packet_dir=pdir)
        base = B.season_baselines(qs, m["round_id"])
        base = base[base.model_id == "fc-baseline"].drop(columns="model_id")
        for mid, noise in entries.items():
            df = base.copy()
            prob = df.output_type.isin(["binary", "categorical"])
            df.loc[prob, "value"] = np.clip(df.loc[prob, "value"] + rng.normal(0, noise, prob.sum()),
                                            0.01, 0.99)
            # renormalise categoricals
            cat = df.output_type == "categorical"
            df.loc[cat, "value"] = df[cat].groupby("question_id")["value"].transform(lambda v: v / v.sum())
            qv = df.output_type == "quantile"
            df.loc[qv, "value"] = df.loc[qv, "value"] + rng.integers(-3, 4)
            out = root / "model-output" / mid / f"{season}-{d.isoformat()}.csv"
            out.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(out, index=False)

    # Synthetic truth: persistence favourite wins everywhere; seat counts near baseline medians.
    truth = []
    for q in qs:
        if q["output_type"] == "binary":
            truth.append((q["question_id"], 0, "provisional"))
        elif q["output_type"] == "categorical":
            truth.append((q["question_id"], q.get("pres_2024", "R"), "provisional"))
        else:
            truth.append((q["question_id"], 213 if "house" in q["question_id"] else 47, "provisional"))
    tdir = root / "target-data" / "truth" / season
    tdir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(truth, columns=["question_id", "y", "status"]).assign(vintage="demo").to_csv(
        tdir / "truth.csv", index=False)

    board = season_eval.run(root, season, n_boot=50, min_days=2)
    season_board.build(root / "eval" / "out" / season / "board.json",
                       root / "leaderboard" / f"{season}.html")
    rs = pd.DataFrame(board["relative_skill"])
    print(rs[["model_id", "tier", "rel_skill", "lo95", "hi95", "n_days", "provisional"]]
          .sort_values(["tier", "rel_skill"]).to_string(index=False))
    print(f"\nboard: {root / 'leaderboard' / f'{season}.html'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--root")
    ap.add_argument("--days", type=int, default=3)
    a = ap.parse_args()
    root = Path(a.root) if a.root else Path(tempfile.mkdtemp(prefix="fc-demo-"))
    main(root, a.days)
