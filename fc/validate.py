"""Validate a model-output file against hub-config/tasks.json (DESIGN.md §6.1, §8.4).

The tasks.json here is a hubverse-shaped config reduced to what this scaffold needs:
{
  "rounds": [{
    "round_id": "...",
    "quantile_levels": [...],
    "questions": [{"question_id": "...", "output_type": "quantile"|"binary"|"categorical",
                   "core": true, "quantile_levels": [...], "bounds": [lo, hi],
                   "categories": ["D", "R", "Other"]}]
  }]
}

Per-question `quantile_levels` override the round default; `categorical` rows carry one
category per `output_type_id` and must sum to 1 (rules 2026.10 §6).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

REQUIRED_COLS = ["round_id", "question_id", "output_type", "output_type_id", "value"]


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def __str__(self) -> str:
        lines = [f"ERROR   {e}" for e in self.errors] + [f"WARNING {w}" for w in self.warnings]
        return "\n".join(lines) if lines else "OK"


def load_tasks(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def round_config(tasks: dict, round_id: str) -> dict:
    for r in tasks["rounds"]:
        if r["round_id"] == round_id:
            return r
    raise KeyError(round_id)


def read_output(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if p.suffix == ".parquet":
        return pd.read_parquet(p)
    if p.suffix == ".csv":
        return pd.read_csv(p)
    raise ValueError(f"unsupported extension {p.suffix}")


def validate_frame(df: pd.DataFrame, rc: dict) -> Report:
    rep = Report()
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        rep.errors.append(f"missing columns {missing}")
        return rep
    if (df["round_id"] != rc["round_id"]).any():
        rep.errors.append("round_id mismatch")
    qspec = {q["question_id"]: q for q in rc["questions"]}
    unknown = sorted(set(df["question_id"]) - set(qspec))
    if unknown:
        rep.errors.append(f"unknown question_id(s): {unknown[:5]}{'...' if len(unknown) > 5 else ''}")
    if df["value"].isna().any() or not np.isfinite(df["value"].astype(float)).all():
        rep.errors.append("non-finite values present")

    default_levels = np.asarray(rc.get("quantile_levels", []), dtype=float)
    for qid, g in df.groupby("question_id"):
        spec = qspec.get(qid)
        if spec is None:
            continue
        types = set(g["output_type"])
        if types != {spec["output_type"]}:
            rep.errors.append(f"{qid}: output_type {types} != {spec['output_type']}")
            continue
        if spec["output_type"] == "quantile":
            levels = np.asarray(spec.get("quantile_levels", default_levels), dtype=float)
            got = np.sort(g["output_type_id"].astype(float).to_numpy())
            if len(got) != len(levels) or not np.allclose(got, levels):
                rep.errors.append(f"{qid}: quantile levels do not match config")
                continue
            vals = g.sort_values("output_type_id", key=lambda s: s.astype(float))["value"].to_numpy()
            if np.any(np.diff(vals) < 0):
                rep.errors.append(f"{qid}: quantiles not non-decreasing")
            b = spec.get("bounds")
            if b is not None and (vals.min() < b[0] or vals.max() > b[1]):
                rep.errors.append(f"{qid}: quantiles outside bounds {b}")
        elif spec["output_type"] == "binary":
            if len(g) != 1:
                rep.errors.append(f"{qid}: binary needs exactly one row")
            elif not 0 <= float(g["value"].iloc[0]) <= 1:
                rep.errors.append(f"{qid}: probability outside [0,1]")
        elif spec["output_type"] == "categorical":
            cats = list(spec["categories"])
            got_ids = list(g["output_type_id"].astype(str))
            if sorted(got_ids) != sorted(cats):
                rep.errors.append(f"{qid}: categories {sorted(got_ids)} != {sorted(cats)}")
                continue
            p = g["value"].astype(float).to_numpy()
            if (p < 0).any() or (p > 1).any():
                rep.errors.append(f"{qid}: probability outside [0,1]")
            elif not np.isclose(p.sum(), 1.0, atol=1e-6):
                rep.errors.append(f"{qid}: categorical probabilities sum to {p.sum():.6f}, not 1")
    core = [q["question_id"] for q in rc["questions"] if q.get("core")]
    missing_core = sorted(set(core) - set(df["question_id"]))
    if missing_core:
        rep.warnings.append(
            f"{len(missing_core)} core question(s) missing; they will be worst-case imputed (§8.4)"
        )
    return rep


def validate_file(path: str | Path, tasks_path: str | Path, round_id: str | None = None) -> Report:
    df = read_output(path)
    tasks = load_tasks(tasks_path)
    rid = round_id or Path(path).stem
    return validate_frame(df, round_config(tasks, rid))
