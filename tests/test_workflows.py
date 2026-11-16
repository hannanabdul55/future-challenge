"""Static checks on the GitHub workflows the hub depends on (parsed, never executed)."""

from __future__ import annotations

from pathlib import Path

import yaml

WF = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def load(name: str) -> tuple[dict, str]:
    text = (WF / name).read_text()
    return yaml.safe_load(text), text


def triggers(doc: dict) -> dict:
    # PyYAML (YAML 1.1) parses the bare key `on` as boolean True.
    return doc.get("on", doc.get(True))


def steps(doc: dict) -> list[dict]:
    return [s for job in doc["jobs"].values() for s in job["steps"]]


def test_daily_round_opens_the_rolling_season():
    doc, _ = load("daily-round.yml")
    assert doc["env"]["SEASON"] == "2026-27-rolling"


def test_watchdog_schedule_is_commit_check_then_reveal_check():
    doc, _ = load("watchdog.yml")
    crons = {s["cron"] for s in triggers(doc)["schedule"]}
    assert crons == {"15 2 * * *", "45 18 * * *"}


def test_watchdog_is_read_only_and_dispatchable():
    doc, text = load("watchdog.yml")
    assert doc["permissions"] == {"contents": "read"}
    assert "contents: write" not in text
    inputs = triggers(doc)["workflow_dispatch"]["inputs"]
    assert {"check", "test_alert"} <= set(inputs)
    assert inputs["check"]["options"] == ["commit", "reveal"]
    assert inputs["check"]["default"] == "commit"
    assert inputs["test_alert"]["default"] is False
    assert doc["env"]["SEASON"] == "2026-27-rolling"


def test_webhook_secret_only_reaches_the_script_through_env():
    doc, text = load("watchdog.yml")
    assert text.count("secrets.ALERT_WEBHOOK") == 1
    holders = [s for s in steps(doc) if "secrets.ALERT_WEBHOOK" in str(s.get("env", {}))]
    assert len(holders) == 1
    env = holders[0]["env"]
    assert "secrets.ALERT_WEBHOOK" in env["ALERT_WEBHOOK"]
    run = holders[0]["run"]
    assert "${{" not in run                      # no expression interpolation in the script
    assert "python3 -m fc.alert" in run
    for s in steps(doc):
        assert "secrets." not in s.get("run", "")
    # The script never prints the secret itself.
    assert 'echo "$ALERT_WEBHOOK' not in run and "echo $ALERT_WEBHOOK" not in run
    assert set(env) >= {"ALERT_WEBHOOK", "CHECK_INPUT", "TEST_ALERT", "SCHEDULE"}


def test_watchdog_checks_the_same_artifacts_the_job_publishes():
    doc, _ = load("watchdog.yml")
    run = next(s["run"] for s in steps(doc) if "run" in s)
    assert "rounds/$SEASON/$DAY/manifest.json" in run
    assert "rounds/$SEASON/$DAY/roots/*.json" in run
    assert "runs/$SEASON/$DAY/*/bundle.json" in run
    assert "GPU box job missing or failed" in run
    assert "exit 1" in run
