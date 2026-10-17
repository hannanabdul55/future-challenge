"""Registration, deadlines, per-wave schedule and ship gate for phase 04.1 (SC-3, D-06, D-19).

Everything is offline and deterministic: `now` is always passed explicitly.
"""

from __future__ import annotations

import ast
import datetime as dt
import json
from pathlib import Path

import pytest

from fc.model_identity import prompt_template_hash
from fc.train import gate, schedule

REPO = Path(__file__).resolve().parents[1]
REG_PATH = REPO / "data" / "phase-04.1" / "registration.json"


def utc(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s)


@pytest.fixture(scope="module")
def reg() -> dict:
    return json.loads(REG_PATH.read_text())


DAYS = [
    "2024-10-28", "2024-11-01", "2024-11-05",
    "2024-10-29", "2024-10-30", "2024-10-31", "2024-11-02", "2024-11-03", "2024-11-04",
]
BEFORE = utc("2026-10-22T12:00:00Z")
AFTER = utc("2026-10-23T05:00:00Z")


def system(parse=0.97, primary=0.5, days=DAYS[:3], modes_ok=True, gguf_ok=True, valid=True):
    return {
        "valid": valid,
        "gguf_ok": gguf_ok,
        "days": {d: {"parse_rate": parse, "primary": primary, "modes_ok": modes_ok} for d in days},
    }


def decide(reg, tuned, base, now=BEFORE):
    return gate.ship_decision({"tuned": tuned, "base": base}, now=now, registration=reg)


# ------------------------------------------------------------------ ship gate

def test_no_tuned_result_ships_base(reg):
    out = decide(reg, None, system())
    assert (out["ship"], out["reason"]) == ("base", "tuned_unavailable")


def test_parse_below_base_ships_base_even_when_log_is_better(reg):
    out = decide(reg, system(parse=0.96, primary=0.1), system(parse=0.97, primary=0.5))
    assert (out["ship"], out["reason"]) == ("base", "parse_below_base")


def test_tuned_ships_when_parse_ge_and_log_le(reg):
    out = decide(reg, system(parse=0.98, primary=0.4), system(parse=0.97, primary=0.5))
    assert out["ship"] == "tuned"
    assert out["compared_days"] == DAYS[:3]
    assert out["parse"]["tuned"] == pytest.approx(0.98)
    assert out["primary"]["base"] == pytest.approx(0.5)


def test_equal_values_ship_tuned(reg):
    out = decide(reg, system(parse=0.97, primary=0.5), system(parse=0.97, primary=0.5))
    assert out["ship"] == "tuned"


def test_log_worse_ships_base(reg):
    out = decide(reg, system(parse=0.99, primary=0.6), system(parse=0.97, primary=0.5))
    assert (out["ship"], out["reason"]) == ("base", "log_worse")


def test_deadline_without_tuned_result_ships_base(reg):
    out = decide(reg, None, system(), now=AFTER)
    assert (out["ship"], out["reason"]) == ("base", "deadline")


def test_base_gguf_failed_and_tuned_unavailable_ships_base_public(reg):
    out = decide(reg, None, system(gguf_ok=False))
    assert out["ship"] == "base-public"


def test_failed_tuned_gguf_is_unavailable(reg):
    out = decide(reg, system(gguf_ok=False), system())
    assert (out["ship"], out["reason"]) == ("base", "tuned_unavailable")


@pytest.mark.parametrize("who", ["tuned", "base"])
def test_thinking_confound_invalidates_a_system(reg, who):
    bad = system(modes_ok=False)
    tuned, base = (bad, system()) if who == "tuned" else (system(parse=0.99, primary=0.1), bad)
    out = decide(reg, tuned, base)
    assert out["ship"] == "base"
    assert out["reason"] == "thinking_confound"


def test_compared_days_is_the_intersection_in_registered_order(reg):
    got = gate.compared_days(reg, set(DAYS[:5]), set(DAYS[2:7]))
    assert got == DAYS[2:5]
    assert gate.compared_days(reg, set(), set(DAYS)) == []


def test_fewer_than_the_three_first_ordered_days_is_insufficient(reg):
    out = decide(reg, system(days=DAYS[:2] + DAYS[3:6]), system(days=DAYS))
    assert (out["ship"], out["reason"]) == ("base", "insufficient_days")
    out = decide(reg, system(days=DAYS), system(days=DAYS))
    assert out["ship"] == "tuned" and out["compared_days"] == DAYS


def test_gate_constants_agree_with_registration(reg):
    ev = reg["eval"]
    assert ev["held_out"] == 2024
    assert ev["day_order"][:3] == ["2024-10-28", "2024-11-01", "2024-11-05"]
    assert sorted(ev["day_order"]) == reg["windows"]["cycles"]["2024"]["days"]
    assert ev["gate"]["parse"] == "tuned >= base" and ev["gate"]["primary"] == "tuned <= base"
    assert ev["gate"]["min_first_ordered_days"] == 3


# ------------------------------------------------------------------ deadlines

def test_deadline_values_and_order(reg):
    assert schedule.RUN_A_DEADLINE_UTC == utc("2026-10-21T17:00:00Z")
    assert schedule.TRAIN_CUTOFF_UTC == utc("2026-10-22T05:00:00Z")
    assert schedule.FALLBACK_DECISION_UTC == utc("2026-10-23T05:00:00Z")
    assert schedule.FREEZE_DEADLINE_UTC == utc("2026-10-26T00:00:00Z")
    chain = [schedule.RUN_A_START_BY_UTC, schedule.RUN_A_DEADLINE_UTC, schedule.TRAIN_CUTOFF_UTC,
             schedule.FALLBACK_DECISION_UTC, schedule.FREEZE_DEADLINE_UTC]
    assert chain == sorted(set(chain))
    sc = reg["schedule"]
    assert schedule.TRAIN_CUTOFF_UTC == utc(sc["train_cutoff_utc"])
    assert schedule.RUN_A_START_BY_UTC == utc(sc["run_a_start_by_utc"])


def test_train_and_run_a_gates():
    eps = dt.timedelta(seconds=1)
    assert schedule.train_allowed(schedule.TRAIN_CUTOFF_UTC - eps)
    assert not schedule.train_allowed(schedule.TRAIN_CUTOFF_UTC)
    assert schedule.run_a_allowed(schedule.RUN_A_START_BY_UTC - eps)
    assert not schedule.run_a_allowed(schedule.RUN_A_START_BY_UTC)
    assert not schedule.past_fallback_decision(schedule.FALLBACK_DECISION_UTC - eps)
    assert schedule.past_fallback_decision(schedule.FALLBACK_DECISION_UTC)


# ------------------------------------------------------------------ schedule file

def test_waves_cover_every_plan_once(reg):
    waves = reg["schedule"]["waves"]
    assert [w["wave"] for w in waves] == list(range(1, 10))
    plans = sorted(p for w in waves for p in w["plans"])
    assert plans == [f"04.1-{i:02d}" for i in range(1, 16)]
    assert waves[0]["plans"] == ["04.1-01", "04.1-02", "04.1-15"]
    for w in waves:
        lo, hi = w["expected_hours"]
        assert lo <= hi
    done = [utc(w["latest_done_utc"]) for w in waves]
    assert done == sorted(done)
    assert done[4] + dt.timedelta(hours=1) <= utc(reg["schedule"]["run_a_start_by_utc"])


def test_slippage_rules_s0_to_s5(reg):
    assert sorted(reg["schedule"]["slippage_rules"]) == ["S0", "S1", "S2", "S3", "S4", "S5"]


def test_wave_5_overrun_applies_s1():
    before = schedule.wave_status(utc("2026-10-20T17:59:59Z"), 5)
    assert before["overrun"] is False
    at = schedule.wave_status(utc("2026-10-20T18:00:00Z"), 5)
    assert at["overrun"] is True and at["rule"] == "S1"
    assert at["latest_done_utc"] == "2026-10-20T18:00:00Z"


def test_wave_3_overrun_applies_s0_with_projection():
    now = utc("2026-10-20T10:00:00Z")
    st = schedule.wave_status(now, 3)
    assert st["overrun"] is True and st["rule"] == "S0"
    # high estimates of waves 4 and 5 (2.5 h + 6 h) plus the 1 h SFT build
    assert utc(st["projected_run_a_start_utc"]) == now + dt.timedelta(hours=9.5)
    assert st["plans"] == ["04.1-05", "04.1-13"]
    assert st["expected_hours"] == [2, 4]


def test_wave_status_keys_and_late_waves():
    st = schedule.wave_status(utc("2026-10-17T23:00:00Z"), 1)
    assert set(st) >= {"wave", "plans", "expected_hours", "latest_done_utc", "overrun", "rule",
                       "action", "projected_run_a_start_utc"}
    assert st["overrun"] is False and st["rule"] is None
    late = schedule.wave_status(utc("2026-10-23T06:00:00Z"), 8)
    assert late["overrun"] is True and late["rule"] == "S4"
    assert "projected_run_a_start_utc" not in late
    with pytest.raises(ValueError):
        schedule.wave_status(utc("2026-10-17T23:00:00Z"), 10)


def test_achievable(reg):
    assert schedule.achievable(reg) is True
    assert reg["schedule"]["achievable_at_registration"] is True
    late = json.loads(json.dumps(reg))
    late["registered_at"] = "2026-10-20T12:00:00Z"
    assert schedule.achievable(late) is False


# ------------------------------------------------------------------ registration file

@pytest.mark.parametrize("year,election", [
    (2018, "2018-11-06"), (2020, "2020-11-03"), (2022, "2022-11-08"), (2024, "2024-11-05"),
])
def test_windows_are_election_day_minus_eight_through_election_day(reg, year, election):
    e = dt.date.fromisoformat(election)
    expected = [(e - dt.timedelta(days=k)).isoformat() for k in range(8, -1, -1)]
    cyc = reg["windows"]["cycles"][str(year)]
    assert cyc["election"] == election
    assert cyc["days"] == expected and len(expected) == 9


def test_template_hash_matches_prompt_file(reg):
    data = (REPO / "fc" / "prompts" / "forecaster-v1.md").read_bytes()
    assert reg["inference"]["template_sha256"] == prompt_template_hash(data)


def test_registration_header_and_sections(reg):
    assert reg["schema"] == "fc-train-registration/2026.10"
    assert reg["amendments"] == []
    assert reg["base"]["revision"] == "6e5971d9eba42665f5bd5a0fcf047f299ce1dccc"
    assert reg["training"]["run_a"]["select_epoch_from"] == [1, 2, 3]
    assert reg["eval"]["metrics"]["primary"]["clip"] == 0.001
    assert "fallback" in reg and "base-public" in json.dumps(reg["fallback"])
    assert utc(reg["registered_at"]) < utc(reg["schedule"]["run_a_start_by_utc"])


def test_registration_path_is_repo_data_file():
    assert schedule.REGISTRATION_PATH == REG_PATH


def test_schedule_and_gate_import_only_stdlib_and_each_other():
    allowed_from_train = {"schedule"}
    for name in ("schedule.py", "gate.py"):
        tree = ast.parse((REPO / "fc" / "train" / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    mods = [("." * node.level) + (node.module or a.name) for a in node.names]
                else:
                    mods = [node.module or ""]
            else:
                continue
            for m in mods:
                top = m.lstrip(".").split(".")[0]
                if m.startswith(".") or top == "fc":
                    leaf = m.lstrip(".").split(".")[-1]
                    assert name == "gate.py" and leaf in allowed_from_train, (name, m)
                    continue
                assert top in {"__future__", "argparse", "datetime", "json", "pathlib", "sys"}, (
                    name, m)
