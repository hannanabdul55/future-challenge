"""One SFT example per (cycle, day, runner batch): prompt, completion, mask spans.

The prompt is built by ``fc.forecast_io.build_prompt`` over the batch ``F.batches`` hands the
runner, so its bytes equal what ``runner/forecast/forecast.py`` sends. The completion is one
JSON object keyed by question id in batch order and must parse through ``F.parse_answer`` at
rate 1.0. House answers are kept in the completion but covered by mask spans (D-13): the
tokeniser step gives their characters label -100, so the model learns the format and nothing
about House levels. No probability is ever exactly 0 or 1 (D-05).
"""

from __future__ import annotations

import json
from typing import Any

from fc import forecast_io as F
from fc import questions as Q

from .history import Cycle

HOUSE_IDS = ("control-house", "seats-house-d")

_SEP = (", ", ": ")
# Normal quantile z-scores at Q.SEAT_LEVELS; the neutral House answer is centred on 218.
_Z = {0.025: -1.96, 0.1: -1.28, 0.25: -0.67, 0.4: -0.25, 0.5: 0.0,
      0.6: 0.25, 0.75: 0.67, 0.9: 1.28, 0.975: 1.96}


def _neutral_seats() -> dict[str, int]:
    vals: list[int] = []
    for lv in Q.SEAT_LEVELS:
        v = round(218 + 12 * _Z[lv])
        vals.append(max(v, vals[-1]) if vals else v)  # non-decreasing
    return {str(lv): v for lv, v in zip(Q.SEAT_LEVELS, vals)}


NEUTRAL_HOUSE: dict[str, dict[str, Any]] = {
    "control-house": {"p": 0.5},
    "seats-house-d": _neutral_seats(),
}


def round_probs(d: dict[str, float]) -> dict[str, float]:
    """Round to 2 decimals, floor each class at 0.01, make the sum exactly 1.00 by adjusting
    the largest class. Works in integer hundredths so the sum cannot drift."""
    cents = {k: max(1, round(v * 100)) for k, v in d.items()}
    top = max(cents, key=lambda k: (cents[k], k))
    cents[top] += 100 - sum(cents.values())
    if min(cents.values()) < 1:
        raise ValueError(f"cannot form a valid distribution from {d!r}")
    return {k: v / 100 for k, v in cents.items()}


def completion_text(batch: list[dict[str, Any]],
                    answers: dict[str, dict[str, Any]]) -> tuple[str, dict[str, tuple[int, int]]]:
    """The completion JSON and, per question id, the [start, end) character range of its
    value object."""
    parts: list[str] = ["{"]
    spans: dict[str, tuple[int, int]] = {}
    pos = 1
    for n, q in enumerate(batch):
        qid = q["question_id"]
        key = json.dumps(qid) + _SEP[1]
        value = json.dumps(answers[qid], separators=_SEP)
        if n:
            parts.append(_SEP[0])
            pos += len(_SEP[0])
        parts.append(key)
        pos += len(key)
        spans[qid] = (pos, pos + len(value))
        parts.append(value)
        pos += len(value)
    parts.append("}")
    return "".join(parts), spans


def build_example(*, cycle: Cycle, day: Any, packet_md: str, batch: list[dict[str, Any]],
                  batch_index: int, answers: dict[str, dict[str, Any]],
                  masked_ids: list[str] | tuple[str, ...], template: str) -> dict[str, Any]:
    family = batch[0]["family"]
    ids = [q["question_id"] for q in batch]
    wanted = set(masked_ids) | set(HOUSE_IDS)
    masked = [i for i in ids if i in wanted]
    text, spans = completion_text(batch, answers)
    return {
        "id": f"{cycle.year}-{day.isoformat()}-{family}-{batch_index}",
        "cycle": cycle.year,
        "day": day.isoformat(),
        "family": family,
        "batch_index": batch_index,
        "question_ids": ids,
        "prompt": F.build_prompt(template, packet_md=packet_md, questions=batch,
                                 day=day.isoformat()),
        "completion": text,
        "masked_ids": masked,
        "mask_spans": [list(spans[i]) for i in masked],
    }


def check_example(example: dict[str, Any], batch: list[dict[str, Any]], round_id: str) -> None:
    """Raise ValueError unless the completion round-trips through the live parser."""
    parsed = F.parse_answer(example["completion"], batch, round_id=round_id)
    if parsed.missing:
        raise ValueError(f"{example['id']}: parser missed {parsed.missing}")
    if parsed.parse_rate != 1.0:
        raise ValueError(f"{example['id']}: parse_rate {parsed.parse_rate}")
    for r in parsed.rows:
        if r["output_type"] in ("binary", "categorical") and r["value"] in (0.0, 1.0):
            raise ValueError(f"{example['id']}: raw 0/1 value for {r['question_id']} (D-05)")
    ids = set(example["question_ids"])
    for h in HOUSE_IDS:
        if h in ids and h not in example["masked_ids"]:
            raise ValueError(f"{example['id']}: House answer {h} is not masked (D-13)")
    if len(example["masked_ids"]) != len(example["mask_spans"]):
        raise ValueError(f"{example['id']}: masked_ids and mask_spans differ in length")
    for a, b in example["mask_spans"]:
        json.loads(example["completion"][a:b])  # a mask span must be exactly one value object


def tokenize_example(example: dict[str, Any], tokenizer: Any, *,
                     eos_id: int = 100257) -> dict[str, Any]:
    """Pre-tokenise one example for the trainer: input_ids, labels, attention_mask.

    The prompt is rendered with the chat template and ``add_generation_prompt=True`` (the
    render proven equal to llama.cpp's in plan 04.1-03) and tokenised on its own with
    ``add_special_tokens=False``; the completion is tokenised separately, so the prompt ids
    equal what llama.cpp feeds at inference and no BPE merge crosses the boundary. Prompt
    labels are -100. A completion token keeps its id as label unless its character range
    overlaps a mask span (D-13). ``eos_id`` is appended with a real label so the model
    learns to stop. ``tokenizer`` is duck-typed (apply_chat_template, __call__).
    """
    rendered = tokenizer.apply_chat_template(
        [{"role": "user", "content": example["prompt"]}],
        add_generation_prompt=True, tokenize=False)
    prompt_ids = list(tokenizer(rendered, add_special_tokens=False)["input_ids"])
    comp = tokenizer(example["completion"], add_special_tokens=False,
                     return_offsets_mapping=True)
    comp_ids = list(comp["input_ids"])
    offsets = list(comp["offset_mapping"])
    spans = [(int(a), int(b)) for a, b in example.get("mask_spans", [])]
    labels = [-100] * len(prompt_ids)
    for tid, (a, b) in zip(comp_ids, offsets, strict=True):
        hidden = any(a < e and b > s for s, e in spans)
        labels.append(-100 if hidden else tid)
    input_ids = prompt_ids + comp_ids + [eos_id]
    labels.append(eos_id)
    n_label = sum(1 for x in labels if x != -100)
    if n_label == 0:
        raise ValueError(f"{example.get('id')}: no label tokens")
    return {"input_ids": input_ids, "labels": labels,
            "attention_mask": [1] * len(input_ids),
            "n_prompt": len(prompt_ids), "n_completion": len(comp_ids) + 1,
            "n_label": n_label}
