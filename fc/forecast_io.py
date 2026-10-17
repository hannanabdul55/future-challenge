"""Prompt construction and answer parsing for hub-run forecasters (RUN-01, RUN-04).

The hub, not the participant, decides what a model is asked and how its answer is read.
Both halves live here so Tier S (reference image) and Tier A (self-run) use the same
prompt bytes and the same parser, and so the prompt template's hash can be pinned in an
identity record (`model_identity.prompt_template_sha256`).

Output contract: the model answers with one JSON object per question batch:

    {"control-house": {"p": 0.42},
     "senate-ga":     {"D": 0.55, "R": 0.43, "Other": 0.02},
     "seats-house-d": {"0.025": 200, "0.1": 206, ..., "0.975": 231}}

The parser is forgiving (code fences, prose around the JSON, trailing commas, numbers as
strings, percentages) and strict where it matters: an unparseable or incomplete answer to a
question is *missing*, never guessed.

Which JSON is read: only the text after the last ``</think>`` (thinking models draft inside
the think block), and of the objects there the last one that contains an expected question
id. A binary answer may be a bare number or percent string instead of ``{"p": ...}``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

TEMPLATE_PATH = Path(__file__).parent / "prompts" / "forecaster-v1.md"

OUTPUT_COLS = ["round_id", "question_id", "output_type", "output_type_id", "value"]


def sha256_text(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


# --------------------------------------------------------------------------- prompt

def load_template(path: Path = TEMPLATE_PATH) -> str:
    return path.read_text()


def question_block(q: dict[str, Any]) -> str:
    qid, t = q["question_id"], q["output_type"]
    if t == "binary":
        fmt = '{"p": <probability that the answer is YES, 0..1>}'
    elif t == "categorical":
        cats = ", ".join(f'"{c}": <prob>' for c in q["categories"])
        fmt = "{" + cats + "}  (probabilities sum to 1)"
    else:
        lv = ", ".join(f'"{x}": <value>' for x in q["quantile_levels"])
        b = q.get("bounds")
        fmt = "{" + lv + "}" + (f"  (integers in [{b[0]}, {b[1]}], non-decreasing)" if b else "")
    return f'- "{qid}": {q["statement"]}\n  answer format: {fmt}'


def build_prompt(template: str, *, packet_md: str, questions: list[dict[str, Any]],
                 day: str) -> str:
    """Fill the template. Placeholders: {{DAY}}, {{PACKET}}, {{QUESTIONS}}."""
    qtext = "\n".join(question_block(q) for q in questions)
    return (template.replace("{{DAY}}", day).replace("{{PACKET}}", packet_md)
            .replace("{{QUESTIONS}}", qtext))


def batches(questions: list[dict[str, Any]], *, max_per_batch: int = 20) -> list[list[dict]]:
    """Group by family so a batch is homogeneous and fits a small context."""
    out: list[list[dict]] = []
    for fam in sorted({q["family"] for q in questions}):
        fam_qs = [q for q in questions if q["family"] == fam]
        for i in range(0, len(fam_qs), max_per_batch):
            out.append(fam_qs[i:i + max_per_batch])
    return out


# --------------------------------------------------------------------------- parse

_NUM = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?)\s*(%?)\s*$")


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = _NUM.match(v)
        if m:
            x = float(m.group(1))
            return x / 100.0 if m.group(2) else x
    return None


def _balanced_end(text: str, start: int) -> int | None:
    """Index of the brace closing the object that opens at ``start`` (string-aware)."""
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


def extract_json(text: str, expected_ids: list[str] | None = None) -> dict[str, Any] | None:
    """The model's final JSON answer. Only the text after the last ``</think>`` is read (a
    thinking model drafts answers inside the think block). Top-level objects are scanned left
    to right, tolerating fences, prose and trailing commas; the last one containing an
    expected question id wins, else the last object of any kind. Fences need no separate pass
    because the scan finds objects inside them too."""
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    found: list[dict[str, Any]] = []
    i = 0
    while True:
        start = text.find("{", i)
        if start < 0:
            break
        end = _balanced_end(text, start)
        obj = None
        if end is not None:
            blob = re.sub(r",\s*([}\]])", r"\1", text[start:end + 1])
            try:
                obj = json.loads(blob)
            except json.JSONDecodeError:
                obj = None
        if isinstance(obj, dict):
            found.append(obj)
            i = end + 1  # type: ignore[operator]
        else:
            i = start + 1
    if not found:
        return None
    if expected_ids:
        wanted = set(expected_ids)
        for obj in reversed(found):
            if wanted & obj.keys():
                return obj
    return found[-1]


@dataclass
class Parsed:
    rows: list[dict[str, Any]] = field(default_factory=list)
    missing: dict[str, str] = field(default_factory=dict)   # question_id -> reason

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows, columns=OUTPUT_COLS)

    @property
    def parse_rate(self) -> float:
        n = len(self.rows and {r["question_id"] for r in self.rows}) + len(self.missing)
        return 0.0 if n == 0 else 1 - len(self.missing) / n


def parse_answer(text: str, questions: list[dict[str, Any]], *, round_id: str) -> Parsed:
    out = Parsed()
    obj = extract_json(text, [q["question_id"] for q in questions]) or {}
    for q in questions:
        qid, t = q["question_id"], q["output_type"]
        ans = obj.get(qid)
        if t == "binary" and not isinstance(ans, dict) and _num(ans) is not None:
            ans = {"p": ans}  # a bare number (or "68%") is the probability
        if not isinstance(ans, dict):
            out.missing[qid] = "no JSON object for question"
            continue
        if t == "binary":
            p = _num(ans.get("p", ans.get("yes", ans.get("probability"))))
            if p is None or not 0 <= p <= 1:
                out.missing[qid] = "binary probability missing or outside [0,1]"
                continue
            out.rows.append(_row(round_id, qid, t, "p", p))
        elif t == "categorical":
            cats = q["categories"]
            vals = {c: _num(ans.get(c)) for c in cats}
            if any(v is None or v < 0 for v in vals.values()):
                out.missing[qid] = "categorical probabilities missing or negative"
                continue
            tot = sum(vals.values())  # type: ignore[arg-type]
            if tot <= 0 or abs(tot - 1) > 0.05:
                out.missing[qid] = f"categorical probabilities sum to {tot:.3f}"
                continue
            for c in cats:
                out.rows.append(_row(round_id, qid, t, c, vals[c] / tot))  # type: ignore[operator]
        else:
            levels = [float(x) for x in q["quantile_levels"]]
            got = {}
            keyed = {k: _num(k) for k in ans}
            for lv in levels:
                key = next((k for k, kn in keyed.items() if kn is not None and abs(kn - lv) < 1e-9),
                           None)
                v = _num(ans.get(key)) if key is not None else None
                if v is None:
                    break
                got[lv] = v
            if len(got) != len(levels):
                out.missing[qid] = "quantile levels incomplete"
                continue
            vals = np.array([got[lv] for lv in levels])
            vals = np.maximum.accumulate(vals)  # enforce monotone (hubverse practice)
            b = q.get("bounds")
            if b is not None:
                vals = np.clip(vals, b[0], b[1])
            for lv, v in zip(levels, vals):
                out.rows.append(_row(round_id, qid, t, str(lv), float(v)))
    return out


def _row(round_id: str, qid: str, t: str, tid: str, v: float) -> dict[str, Any]:
    return {"round_id": round_id, "question_id": qid, "output_type": t,
            "output_type_id": tid, "value": float(v)}
