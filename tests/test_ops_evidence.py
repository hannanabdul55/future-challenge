"""OPS-03 evidence gate: python -m fc.ops_evidence (hub-run tier, 7 of 10 days)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from fc import commit as C
from fc import ops_evidence as O
from fc.run import RUN_DOMAIN_SEP

SEASON = "2026-27-rolling"
START = "2026-11-24"
LAST = "2026-12-03"
FINAL = "2026-12-03T18:30:00Z"
IDENTITY = "ab" * 32


def days(n: int = 10, start: str = START) -> list[str]:
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def sign_bundle(sk, bundle: dict) -> dict:
    payload = C.canonical_json({k: v for k, v in bundle.items() if k != "runner_signature"})
    bundle["runner_signature"] = C.sign(sk, RUN_DOMAIN_SEP + payload)
    return bundle


class World:
    def __init__(self, root: Path):
        self.root = root
        self.sk_pem, self.pk_pem = C.generate_keypair()
        self.sk = C.load_private(self.sk_pem)
        (root / "hub-config" / "keys").mkdir(parents=True)
        (root / "hub-config" / "keys" / "runner.pub.pem").write_bytes(self.pk_pem)
        (root / "model-metadata").mkdir()
        (root / "docs" / "ops").mkdir(parents=True)
        self.entries: list[str] = []
        self.set_season(reveal_utc="18:00")
        self.add_entry("house-a", "S")
        self.add_entry("fc-baseline", "H")

    # ---- fixture builders
    def set_season(self, reveal_utc: str = "18:00") -> None:
        d = self.root / "rounds" / SEASON
        d.mkdir(parents=True, exist_ok=True)
        (d / "season.json").write_text(json.dumps({
            "season": SEASON, "first_day": START, "last_day": "2027-03-31",
            "schedule": {"open_utc": "00:00", "commit_utc": "12:00", "reveal_utc": reveal_utc}}))

    def add_entry(self, model_id: str, tier: str) -> None:
        (self.root / "model-metadata" / f"{model_id}.yml").write_text(
            yaml.safe_dump({"model_id": model_id, "tier": tier}))
        if tier == "S":
            self.entries.append(model_id)

    def manifest(self, day: str) -> None:
        d = self.root / "rounds" / SEASON / day
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text(json.dumps({"round_id": f"{SEASON}-{day}"}))

    def bundle(self, day: str, model_id: str = "house-a", *, status: str = "ok",
               parse_rate: float | None = 1.0, sign: bool = True, **extra) -> Path:
        self.manifest(day)
        b = {"schema": "fc-run/2026.10", "round_id": f"{SEASON}-{day}", "model_id": model_id,
             "identity_hash": IDENTITY, "status": status, "reason": None,
             "parse_rate": parse_rate, "image_id": "sha256:feed", **extra}
        if sign:
            sign_bundle(self.sk, b)
        else:
            b["runner_signature"] = None
        p = self.root / "runs" / SEASON / day / model_id / "bundle.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(b, indent=2))
        return p

    def job(self, day: str, mode: str, *, ok: bool = True, failures=None, **over) -> Path:
        rec = {"schema": "fc-job/v1", "season": SEASON, "day": day, "mode": mode, "ok": ok,
               "failures": failures if failures is not None else [],
               "finished_at": f"{day}T00:40:00Z"}
        rec.update(over)
        p = self.root / "runs" / SEASON / day / f"job-{mode}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rec))
        return p

    def good_day(self, day: str) -> None:
        for e in self.entries:
            self.bundle(day, e)
        self.job(day, "commit")
        self.job(day, "reveal")

    def interventions(self, *affected: str) -> Path:
        rows = "\n".join(f"| 2026-11-30 | 09:00 | {a} | restart box | WSL died | op |"
                         for a in affected)
        p = self.root / "docs" / "ops" / "interventions.md"
        p.write_text("# Interventions\n\n"
                     "| Date (UTC) | Time (UTC) | Season day affected | Action | Reason | By |\n"
                     "|---|---|---|---|---|---|\n" + rows + "\n")
        return p

    def evaluate(self, now: str = FINAL, **kw):
        return O.evaluate(
            self.root, SEASON, START, kw.pop("days", 10), kw.pop("need", 7),
            self.pk_pem, O.load_interventions(self.root / "docs" / "ops" / "interventions.md"),
            O.parse_now(now), **kw)


@pytest.fixture
def world(tmp_path) -> World:
    return World(tmp_path)


def window_of_eight(w: World) -> None:
    ds = days()
    for d in ds[:8]:
        w.good_day(d)
    w.bundle(ds[8], status="missing", parse_rate=None)
    w.job(ds[8], "commit")
    w.job(ds[8], "reveal")
    # ds[9] absent


# --------------------------------------------------------------------------- the gate

def test_eight_of_ten_passes(world):
    window_of_eight(world)
    r = world.evaluate()
    assert r["verdict"] == "PASS" and r["ok_days"] == 8 and r["window"] == 10
    assert len(r["days"]) == 10


def test_edited_or_unsigned_bundle_never_counts(world):
    window_of_eight(world)
    d0, d1 = days()[0], days()[1]
    p = world.bundle(d0, status="missing", parse_rate=None)
    b = json.loads(p.read_text())
    b["status"] = "ok"          # edited after signing
    b["parse_rate"] = 1.0
    p.write_text(json.dumps(b))
    world.bundle(d1, sign=False)  # unsigned
    r = world.evaluate()
    assert r["ok_days"] == 6 and r["verdict"] == "FAIL"
    reasons = {row["day"]: row["reason"] for row in r["days"]}
    assert "signature" in reasons[d0] and "signature" in reasons[d1]


def test_signature_by_another_key_never_counts(world):
    window_of_eight(world)
    other = C.load_private(C.generate_keypair()[0])
    p = world.bundle(days()[2])
    sign_bundle(other, b := json.loads(p.read_text()))
    p.write_text(json.dumps(b))
    r = world.evaluate()
    assert r["ok_days"] == 7
    assert "signature" in {x["day"]: x["reason"] for x in r["days"]}[days()[2]]


def test_parse_rate_floor_is_inclusive_at_080(world):
    window_of_eight(world)
    world.bundle(days()[0], parse_rate=0.79)
    r = world.evaluate()
    row = r["days"][0]
    assert not row["ok"] and "parse_rate" in row["reason"]
    world.bundle(days()[0], parse_rate=0.80)
    assert world.evaluate()["days"][0]["ok"]


def test_non_numeric_parse_rate_does_not_count(world):
    window_of_eight(world)
    world.bundle(days()[0], parse_rate=None)
    row = world.evaluate()["days"][0]
    assert not row["ok"] and "parse_rate" in row["reason"]


def test_job_record_failure_fails_the_day(world):
    window_of_eight(world)
    d = days()[0]
    world.job(d, "commit", ok=False, failures=["parse rate 0.5 below 0.80 for house-a"])
    row = world.evaluate()["days"][0]
    assert not row["ok"] and "job-commit.json" in row["reason"]


def test_missing_reveal_record_fails_the_day(world):
    window_of_eight(world)
    d = days()[1]
    (world.root / "runs" / SEASON / d / "job-reveal.json").unlink()
    row = world.evaluate()["days"][1]
    assert not row["ok"] and "job-reveal.json" in row["reason"]


def test_job_record_for_the_wrong_day_fails_the_day(world):
    window_of_eight(world)
    d = days()[2]
    world.job(d, "reveal", day="2026-11-25")
    row = world.evaluate()["days"][2]
    assert not row["ok"] and "job-reveal.json" in row["reason"]


def test_job_record_ok_cases(world):
    d = days()[0]
    p = world.job(d, "commit")
    assert O.job_record_ok(p, SEASON, d, "commit") == (True, "ok")
    assert not O.job_record_ok(p, SEASON, d, "reveal")[0]            # wrong mode
    assert not O.job_record_ok(p, "other-season", d, "commit")[0]
    assert not O.job_record_ok(world.root / "nope.json", SEASON, d, "commit")[0]
    p.write_text("{not json")
    assert not O.job_record_ok(p, SEASON, d, "commit")[0]
    p = world.job(d, "commit", schema="fc-job/v0")
    assert not O.job_record_ok(p, SEASON, d, "commit")[0]
    p = world.job(d, "commit", ok="yes")                              # truthy is not True
    assert not O.job_record_ok(p, SEASON, d, "commit")[0]
    p = world.job(d, "commit", failures=["x"])                        # ok but with failures
    assert not O.job_record_ok(p, SEASON, d, "commit")[0]


def test_one_intervention_day_still_passes_seven(world):
    ds = days()
    for d in ds[:8]:
        world.good_day(d)
    world.interventions(ds[0])
    r = world.evaluate()
    assert r["ok_days"] == 7 and r["verdict"] == "PASS"
    assert "intervention" in r["days"][0]["reason"]


def test_two_intervention_days_fail_and_declare_the_tier_dead(world, capsys):
    ds = days()
    for d in ds[:8]:
        world.good_day(d)
    world.interventions(ds[0], f"{ds[1]}, {ds[2]}")   # comma-separated affected days
    r = world.evaluate()
    assert r["ok_days"] == 5 and r["verdict"] == "FAIL"
    rc = O.main(["--season", SEASON, "--root", str(world.root), "--now", FINAL])
    out = capsys.readouterr().out
    assert rc == 1
    assert "VERDICT: FAIL 5/10 (need 7)" in out
    assert "SCOPE.md: fewer than 7 of 10 — the hub-run tier is declared dead" in out


def test_load_interventions_parses_rows_and_ignores_header_and_prose(tmp_path):
    p = tmp_path / "i.md"
    p.write_text(
        "# Log\nRule: Season day affected lists 2026-11-25 style dates.\n\n"
        "| Date (UTC) | Time (UTC) | Season day affected | Action | Reason | By |\n"
        "|---|---|---|---|---|---|\n"
        "| 2026-11-26 | 10:00 | 2026-11-26, 2026-11-27 | a | b | c |\n"
        "| not-a-date | 10:00 | 2026-11-28 | a | b | c |\n\n"
        "No interventions recorded.\n")
    assert O.load_interventions(p) == {"2026-11-26", "2026-11-27"}
    assert O.load_interventions(tmp_path / "missing.md") == set()


def test_cutoff_is_timestamp_exact(world, capsys):
    window_of_eight(world)
    argv = ["--season", SEASON, "--root", str(world.root)]
    rc = O.main(argv + ["--now", "2026-12-03T18:29:00Z"])
    out = capsys.readouterr().out
    assert rc == 3 and "VERDICT: PENDING" in out and "8/10" in out
    assert O.main(argv + ["--now", "2026-12-03T18:30:00Z"]) == 0
    assert "VERDICT: PASS 8/10 (need 7)" in capsys.readouterr().out
    assert O.main(argv + ["--now", "2026-12-04"]) == 0       # bare date = 00:00:00Z
    capsys.readouterr()
    assert O.main(argv + ["--now", "2026-12-03"]) == 3
    capsys.readouterr()


def test_season_reveal_time_moves_the_cutoff(world, capsys):
    window_of_eight(world)
    world.set_season(reveal_utc="10:00")
    argv = ["--season", SEASON, "--root", str(world.root)]
    assert O.main(argv + ["--now", "2026-12-03T10:29:59Z"]) == 3
    assert O.main(argv + ["--now", "2026-12-03T10:30:00Z"]) == 0
    capsys.readouterr()


def test_window_cutoff_default_reveal_when_season_json_lacks_it(world):
    (world.root / "rounds" / SEASON / "season.json").write_text(json.dumps({"first_day": START}))
    assert O.window_cutoff(world.root, SEASON, LAST).isoformat() == "2026-12-03T18:30:00+00:00"


def test_parse_now_forms():
    assert O.parse_now("2026-12-03T18:30:00Z").isoformat() == "2026-12-03T18:30:00+00:00"
    assert O.parse_now("2026-12-04").isoformat() == "2026-12-04T00:00:00+00:00"
    assert O.parse_now("2026-12-03T20:30:00+02:00").isoformat() == "2026-12-03T18:30:00+00:00"
    assert O.parse_now("2026-12-03T18:30:00").tzinfo is not None
    with pytest.raises(ValueError):
        O.parse_now("tomorrow")


def test_every_tier_s_entry_must_pass_the_day(world):
    world.add_entry("house-b", "S")
    ds = days()
    for d in ds[:8]:
        world.good_day(d)
    # house-b missing on day 0 and with a signed missing bundle on day 1
    (world.root / "runs" / SEASON / ds[0] / "house-b" / "bundle.json").unlink()
    world.bundle(ds[1], "house-b", status="missing", parse_rate=None)
    r = world.evaluate()
    assert r["ok_days"] == 6 and r["verdict"] == "FAIL"
    assert "house-b" in r["days"][0]["reason"] and "house-b" in r["days"][1]["reason"]


def test_non_tier_s_entries_are_not_required(world):
    world.add_entry("house-c", "A")
    for d in days()[:7]:
        world.good_day(d)
    assert world.evaluate()["verdict"] == "PASS"


def test_no_tier_s_entries_never_passes(world):
    (world.root / "model-metadata" / "house-a.yml").unlink()
    world.entries.clear()
    r = world.evaluate()
    assert r["ok_days"] == 0 and r["verdict"] == "FAIL"
    assert "tier S" in r["days"][0]["reason"]


def test_bundle_for_another_model_or_round_does_not_count(world):
    window_of_eight(world)
    d0, d1 = days()[0], days()[1]
    world.bundle(d0, model_id="house-z")          # signed, but not house-a
    p = world.root / "runs" / SEASON / d0 / "house-a" / "bundle.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text((world.root / "runs" / SEASON / d0 / "house-z" / "bundle.json").read_text())
    world.bundle(d1, round_id="2026-27-rolling-2026-11-20")   # another day's round, re-signed
    r = world.evaluate()
    assert not r["days"][0]["ok"] and "model_id" in r["days"][0]["reason"]
    assert not r["days"][1]["ok"] and "round_id" in r["days"][1]["reason"]


def test_defaults_resolve_against_root_and_start_defaults_to_first_day(world, capsys):
    window_of_eight(world)
    rc = O.main(["--season", SEASON, "--root", str(world.root), "--now", FINAL, "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    j = json.loads(out)
    assert j["verdict"] == "PASS" and j["days"][0]["day"] == START


def test_missing_runner_key_is_a_usage_error(world, capsys):
    (world.root / "hub-config" / "keys" / "runner.pub.pem").unlink()
    rc = O.main(["--season", SEASON, "--root", str(world.root), "--now", FINAL])
    assert rc == 2
    assert "runner" in capsys.readouterr().err


def test_help_mentions_now_and_check_bundle(capsys):
    with pytest.raises(SystemExit) as e:
        O.main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "--now" in out and "--check-bundle" in out


# --------------------------------------------------------------------------- --check-bundle

def test_check_bundle_ok_prints_identity_and_exits_zero(world, capsys):
    p = world.bundle(days()[0], parse_rate=0.96)
    rc = O.main(["--check-bundle", str(p), "--min-parse-rate", "0.95",
                 "--runner-pub", str(world.root / "hub-config" / "keys" / "runner.pub.pem")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "BUNDLE OK" in out and "house-a" in out and "0.96" in out and "sha256:feed" in out


def test_check_bundle_below_floor_fails(world, capsys):
    p = world.bundle(days()[0], parse_rate=0.94)
    rc = O.main(["--check-bundle", str(p), "--min-parse-rate", "0.95", "--root", str(world.root)])
    assert rc == 1
    assert "parse_rate" in capsys.readouterr().out


def test_check_bundle_identity_mismatch_fails(world, capsys):
    p = world.bundle(days()[0])
    assert O.main(["--check-bundle", str(p), "--identity-hash", IDENTITY,
                   "--root", str(world.root)]) == 0
    capsys.readouterr()
    rc = O.main(["--check-bundle", str(p), "--identity-hash", "cd" * 32,
                 "--root", str(world.root)])
    assert rc == 1
    assert "identity_hash" in capsys.readouterr().out


def test_check_bundle_tampered_or_garbage_fails(world, capsys):
    p = world.bundle(days()[0])
    b = json.loads(p.read_text())
    b["identity_hash"] = "cd" * 32
    p.write_text(json.dumps(b))
    assert O.main(["--check-bundle", str(p), "--root", str(world.root)]) == 1
    p.write_text("[1, 2]")
    assert O.main(["--check-bundle", str(p), "--root", str(world.root)]) == 1
    assert O.main(["--check-bundle", str(p) + ".nope", "--root", str(world.root)]) == 1
    capsys.readouterr()


def test_bundle_ok_rejects_non_dict_and_nan(world):
    pk = C.load_public(world.pk_pem)
    assert not O.bundle_ok([], pk)[0]
    b = sign_bundle(world.sk, {"status": "ok", "parse_rate": float("nan")})
    assert not O.bundle_ok(b, pk)[0]
    b = sign_bundle(world.sk, {"status": "ok", "parse_rate": True})
    assert not O.bundle_ok(b, pk)[0]


def test_module_constants_match_the_contract():
    assert O.MIN_PARSE_RATE == 0.80
    assert O.JOB_SCHEMA == "fc-job/v1"
    assert O.EVIDENCE_MARGIN_MIN == 30


def test_shipped_intervention_log_starts_empty():
    p = Path(__file__).resolve().parents[1] / "docs" / "ops" / "interventions.md"
    text = p.read_text()
    assert "Season day affected" in text and "No interventions recorded." in text
    assert O.load_interventions(p) == set()
