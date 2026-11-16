"""Rules text <-> code consistency: the normative docs must say what the code does."""
from __future__ import annotations

import json
from pathlib import Path

from fc import macro

ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_rules_carry_void_after_14_days():
    rules = _read("docs/rules.md")
    assert "void-after-14-days" in rules
    assert "14 days" in rules


def test_macro_void_window_matches_rules():
    assert macro.VOID_AFTER_DAYS == 14
    assert macro.VOID_IF[0] == (
        "release delayed more than 14 days past expected_resolution_date"
    )


def test_generated_questions_match_void_text():
    data = json.loads((ROOT / "questions/2026-27-rolling/questions.json").read_text())
    macro_qs = [q for q in data["questions"] if q.get("template", {}).get("name") == "macro"]
    assert macro_qs, "expected generated macro questions in the rolling season"
    for q in macro_qs:
        assert q["resolution"]["void_if"] == list(macro.VOID_IF), q["question_id"]


def test_rules_describe_tracks_house_and_anchoring():
    rules = _read("docs/rules.md")
    for token in ("ranked", "world", "cluster", "house-", "OpenTimestamps"):
        assert token in rules, token
    rekor_lines = [ln for ln in rules.splitlines() if "Rekor" in ln]
    assert rekor_lines, "rules should say Rekor is deferred"
    for ln in rekor_lines:
        assert "deferred" in ln, ln
