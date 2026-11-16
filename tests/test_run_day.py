"""End-to-end tests for scripts/run_day.sh, the GPU box's fail-loud daily job (D-05, D-07).

The real script runs against a throwaway git clone with a bare remote. ``uv``, ``docker``,
``nvidia-smi`` and ``systemctl`` are fakes on PATH; the real ``fc.alert`` (reached through the
fake ``uv run python -m fc.alert``) delivers to a local HTTP server, and the real
``fc.ops_evidence.job_record_ok`` reads the job records the script writes.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from fc import ops_evidence

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "run_day.sh"
BASH = os.environ.get("RUN_DAY_BASH", "bash")  # RUN_DAY_BASH=/bin/bash checks macOS bash 3.2
SEASON = "2026-27-rolling"
DAY = "2026-12-01"
TOKEN = "s3cr3t-token"
FAR_FUTURE_COMMIT = "2099-01-01T12:00:00Z"
FAR_FUTURE_REVEAL = "2099-01-01T18:00:00Z"
AFTER_REVEAL = "2099-01-01T18:05:00Z"

FAKE_UV = '''#!/usr/bin/env python3
"""Fake uv for tests/test_run_day.py: logs argv and does just enough of what fc would do."""
import json
import os
import subprocess
import sys
from pathlib import Path

args = sys.argv[1:]
env = os.environ
with open(env["FAKE_CALLS_LOG"], "a") as fh:
    fh.write("uv " + " ".join(args) + "\\n")
if len(args) < 2 or args[0] != "run":
    sys.exit(99)
rest = args[1:]


def opt(name, default=None):
    return rest[rest.index(name) + 1] if name in rest else default


def write(path, text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


if rest[0] == "python":
    if rest[1:3] == ["-m", "fc.anchor"]:
        sys.exit(int(env.get("FAKE_ANCHOR_RC", "0")))
    e = dict(env, PYTHONPATH=env["FAKE_REPO"])
    os.execve(env["FAKE_REAL_PYTHON"], [env["FAKE_REAL_PYTHON"]] + rest[1:], e)
if rest[0] != "fc":
    sys.exit(98)
cmd = rest[1:]
season, day = opt("--season"), opt("--date")
if cmd[:2] == ["packet", "build"]:
    rc = int(env.get("FAKE_PACKET_RC", "0"))
    if rc == 0:
        write(Path(opt("--out")) / "index.json", "{}")
    sys.exit(rc)
if cmd[:2] == ["round", "open"]:
    rc = int(env.get("FAKE_ROUND_OPEN_RC", "0"))
    if rc == 0:
        manifest = {"round_id": f"{season}-{day}", "T_open": f"{day}T00:00:00Z",
                    "T_commit": env["FAKE_T_COMMIT"], "T_reveal": env["FAKE_T_REVEAL"]}
        write(f"rounds/{season}/{day}/manifest.json", json.dumps(manifest))
    sys.exit(rc)
if cmd[0] == "run":
    entry = opt("--entry")
    mid = Path(entry).stem
    with open(env["FAKE_REMOTE_SEEN"], "a") as fh:
        fh.write(subprocess.run(
            ["git", "--git-dir", env["FAKE_REMOTE"], "log", "main", "--format=%s"],
            capture_output=True, text=True).stdout)
    rc = int(env.get("FAKE_RUN_RC", "0"))
    if rc != 0:
        home = env["HOME"]
        sys.stderr.write("Traceback (most recent call last):\\n")
        sys.stderr.write(f'  File "{home}/fc-ops/fc/run.py", line 9, in run_entry\\n')
        sys.stderr.write("RuntimeError: container exited\\n")
        sys.exit(rc)
    write(f"model-output/{mid}/{season}-{day}.csv", "forecast\\n")
    write(f"runs/{season}/{day}/{mid}/transcript.jsonl", "{}\\n")
    write(f"runs/{season}/{day}/{mid}/bundle.json",
          json.dumps({"model_id": mid, "status": "ok",
                      "parse_rate": float(env.get("FAKE_PARSE_RATE", "1.0"))}))
    print(f"{mid} {season}-{day} ok parse_rate={env.get('FAKE_PARSE_RATE', '1.0')}")
    sys.exit(0)
if cmd[0] == "commit":
    rc = int(env.get("FAKE_COMMIT_RC", "0"))
    if rc == 0:
        write(opt("--out"), json.dumps({"commit": "c0ffee"}))
    sys.exit(rc)
def meddle_with_remote():
    if env.get("FAKE_BREAK_REMOTE"):
        subprocess.run(["git", "remote", "set-url", "origin", "/nonexistent/remote.git"],
                       check=True)
    if env.get("FAKE_ADVANCE_REMOTE"):
        other = Path(env["FAKE_REMOTE"]).parent / "other-clone"
        subprocess.run(["git", "clone", "-q", env["FAKE_REMOTE"], str(other)], check=True)
        (other / "elsewhere.txt").write_text("someone else pushed\\n")
        for c in (["add", "-A"], ["-c", "user.name=o", "-c", "user.email=o@e.invalid",
                                   "commit", "-qm", "elsewhere"], ["push", "-q", "origin", "main"]):
            subprocess.run(["git", *c], cwd=other, check=True)


if cmd[:2] == ["service", "submit"]:
    meddle_with_remote()
    rc = int(env.get("FAKE_SUBMIT_RC", "0"))
    if rc == 0:
        write(Path(opt("--round-dir")) / "roots" / "0.json", json.dumps({"root": "ab"}))
    sys.exit(rc)
if cmd[:2] == ["service", "close-reveals"]:
    meddle_with_remote()
    rc = int(env.get("FAKE_CLOSE_RC", "0"))
    if rc == 0:
        write(Path(opt("--round-dir")) / "reveal_report.json", json.dumps({"matched": []}))
    sys.exit(rc)
sys.exit(97)
'''

FAKE_DOCKER = '#!/bin/sh\necho "docker $*" >> "$FAKE_CALLS_LOG"\nexit "${FAKE_DOCKER_RC:-0}"\n'
FAKE_NVIDIA = '#!/bin/sh\necho "${FAKE_FREE_MIB:-12000}"\n'
FAKE_SYSTEMCTL = '#!/bin/sh\necho "systemctl $*" >> "$FAKE_CALLS_LOG"\nexit 0\n'


class _Sink:
    def __init__(self) -> None:
        self.posts: list[bytes] = []
        sink = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", "0"))
                sink.posts.append(self.rfile.read(n))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/hook/{TOKEN}"

    def text(self) -> str:
        return b"\n".join(self.posts).decode()


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True).stdout


@dataclass
class Box:
    root: Path
    work: Path
    remote: Path
    home: Path
    bin: Path
    calls: Path
    remote_seen: Path
    sink: _Sink
    extra_env: dict[str, str] = field(default_factory=dict)

    def run(self, day: str = DAY, *, mode: str | None = None, webhook: bool = True,
            **env: str) -> subprocess.CompletedProcess[str]:
        e = {k: v for k, v in os.environ.items()
             if not k.startswith(("FC_", "FAKE_", "ALERT_", "SEASON", "MODE", "TIMEOUT"))}
        e.update(
            HOME=str(self.home),
            PATH=f"{self.bin}{os.pathsep}{e.get('PATH', '/usr/bin:/bin')}",
            SEASON=SEASON,
            RUNNER_KEY=str(self.home / "keys" / "runner.pem"),
            RUNNER_PUB=str(self.home / "keys" / "runner.pub.pem"),
            HUB_KEY=str(self.home / "keys" / "hub.pem"),
            FC_MANIFEST_WAIT_MIN="0",
            FC_STOP_SETTLE_S="0",
            FAKE_CALLS_LOG=str(self.calls),
            FAKE_REMOTE_SEEN=str(self.remote_seen),
            FAKE_REMOTE=str(self.remote),
            FAKE_REPO=str(REPO),
            FAKE_REAL_PYTHON=sys.executable,
            FAKE_T_COMMIT=FAR_FUTURE_COMMIT,
            FAKE_T_REVEAL=FAR_FUTURE_REVEAL,
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_CONFIG_SYSTEM=os.devnull,
        )
        if webhook:
            e["ALERT_WEBHOOK"] = self.sink.url
        if mode:
            e["MODE"] = mode
        e.update(self.extra_env)
        e.update(env)
        return subprocess.run([BASH, "scripts/run_day.sh", day], cwd=self.work, env=e,
                              capture_output=True, text=True, timeout=120, check=False)

    # --- views ---
    def calls_text(self) -> str:
        return self.calls.read_text() if self.calls.exists() else ""

    def log_text(self, day: str = DAY) -> str:
        return (self.work / "runs" / SEASON / day / "run_day.log").read_text()

    def job(self, mode: str, day: str = DAY) -> Path:
        return self.work / "runs" / SEASON / day / f"job-{mode}.json"

    def remote_files(self) -> list[str]:
        return git(self.remote, "ls-tree", "-r", "main", "--name-only").split()

    def remote_subjects(self) -> list[str]:
        return git(self.remote, "log", "main", "--format=%s").splitlines()


@pytest.fixture
def box(tmp_path: Path):
    root = tmp_path
    work, remote, home, bindir = root / "work", root / "remote.git", root / "home", root / "bin"
    for d in (work / "scripts", home / "keys", bindir):
        d.mkdir(parents=True)
    for name in ("runner.pem", "runner.pub.pem", "hub.pem"):
        (home / "keys" / name).write_text("dummy\n")
    shutil.copy(SCRIPT, work / "scripts" / "run_day.sh")
    (work / "model-metadata").mkdir()
    (work / "model-metadata" / "house-test.yml").write_text(
        "model_id: house-test\ntier: S\nteam: house\n")
    (work / "model-metadata" / "other-h.yml").write_text(
        "model_id: other-h\ntier: H\nteam: other\n")
    season_dir = work / "rounds" / SEASON
    (season_dir / DAY).mkdir(parents=True)
    (season_dir / "season.json").write_text(json.dumps(
        {"first_day": "2026-11-24", "last_day": "2027-03-31"}))
    (season_dir / DAY / "manifest.json").write_text(json.dumps(
        {"round_id": f"{SEASON}-{DAY}", "T_open": f"{DAY}T00:00:00Z",
         "T_commit": FAR_FUTURE_COMMIT, "T_reveal": FAR_FUTURE_REVEAL}))
    for name, body in (("uv", FAKE_UV), ("docker", FAKE_DOCKER),
                       ("nvidia-smi", FAKE_NVIDIA), ("systemctl", FAKE_SYSTEMCTL)):
        p = bindir / name
        p.write_text(body)
        p.chmod(0o755)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    git(work, "init", "-q", "-b", "main")
    git(work, "config", "user.name", "box")
    git(work, "config", "user.email", "box@example.invalid")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "seed")
    git(work, "remote", "add", "origin", str(remote))
    git(work, "push", "-q", "-u", "origin", "main")
    sink = _Sink()
    try:
        yield Box(root, work, remote, home, bindir, root / "calls.log", root / "remote_seen.log",
                  sink)
    finally:
        sink.server.shutdown()
        sink.server.server_close()


# --- the tracer: an entry failure is loud ------------------------------------------------


def test_entry_failure_exits_nonzero_alerts_once_and_records_a_failing_job(box: Box):
    r = box.run(FAKE_RUN_RC="1")
    assert r.returncode == 1, r.stdout + r.stderr
    assert len(box.sink.posts) == 1
    assert "run failed for house-test" in box.sink.text()

    log = box.log_text()
    assert "run failed for house-test" in log
    assert TOKEN not in log and socket.gethostname() not in log
    assert str(box.home) not in log
    assert "~/fc-ops/fc/run.py" in log  # the traceback line, HOME rewritten to ~
    for stream in (r.stdout, r.stderr):
        assert TOKEN not in stream and socket.gethostname() not in stream
        assert str(box.home) not in stream

    rec_path = box.job("commit")
    rec = json.loads(rec_path.read_text())
    assert rec["schema"] == "fc-job/v1" and rec["ok"] is False
    assert rec["season"] == SEASON and rec["day"] == DAY and rec["mode"] == "commit"
    assert any("house-test" in f for f in rec["failures"])
    ok, _ = ops_evidence.job_record_ok(rec_path, SEASON, DAY, "commit")
    assert ok is False


def test_clean_commit_phase_pushes_roots_but_never_plaintext(box: Box):
    r = box.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert box.sink.posts == []
    assert f"commit({SEASON}): {DAY} tier S commitments rooted" in box.remote_subjects()
    files = box.remote_files()
    assert not any(f.startswith(("model-output/", "runs/")) for f in files)
    assert f"rounds/{SEASON}/{DAY}/roots/0.json" in files
    assert f"sidecar/house-test/{SEASON}-{DAY}.json" in files

    rec_path = box.job("commit")
    rec = json.loads(rec_path.read_text())
    assert rec["ok"] is True and rec["failures"] == []
    assert ops_evidence.job_record_ok(rec_path, SEASON, DAY, "commit") == (True, "ok")
    assert TOKEN not in box.log_text()


def test_script_defaults_to_the_rolling_season():
    text = SCRIPT.read_text()
    assert 'SEASON="${SEASON:-2026-27-rolling}"' in text
    assert "fc-job/v1" in text
    assert re.search(r"\|\|\s*true", text) is None


def test_script_text_has_no_xtrace_env_dump_or_sudo():
    code = re.sub(r"(?m)^\s*#.*$", "", SCRIPT.read_text())
    assert not re.search(r"set\s+-[a-zA-Z]*x|xtrace", code)
    assert not re.search(r"(?m)^\s*(env|printenv|export\s+-p|declare\s+-x)\s*$", code)
    assert not re.search(r"\bsudo\b", code)


# --- Task 2: every failure class is loud and recorded -----------------------------------------


def _job(box: Box, mode: str = "commit", day: str = DAY) -> dict:
    return json.loads(box.job(mode, day).read_text())


def _one_alert_containing(box: Box, needle: str) -> None:
    assert len(box.sink.posts) == 1, box.sink.posts
    assert needle in box.sink.text()


def _add_tier_s(box: Box, name: str) -> None:
    (box.work / "model-metadata" / f"{name}.yml").write_text(f"model_id: {name}\ntier: S\n")


def _retire_manifest(box: Box) -> None:
    """Make the repo look like the round was never opened (the box backstop case)."""
    (box.work / "rounds" / SEASON / DAY / "manifest.json").unlink()
    git(box.work, "add", "-A")
    git(box.work, "commit", "-qm", "no manifest")
    git(box.work, "push", "-q", "origin", "main")


def test_min_parse_rate_default_equals_the_published_evidence_threshold():
    m = re.search(r'FC_MIN_PARSE_RATE="\$\{FC_MIN_PARSE_RATE:-([0-9.]+)\}"', SCRIPT.read_text())
    assert m, "FC_MIN_PARSE_RATE default not found"
    assert float(m.group(1)) == ops_evidence.MIN_PARSE_RATE


def _const(name: str) -> int:
    m = re.search(rf"^{name}=(\d+)$", SCRIPT.read_text(), re.MULTILINE)
    assert m, f"{name} must be a plain NAME=value line in run_day.sh"
    return int(m.group(1))


def test_commit_phase_ends_before_the_watchdog_looks():
    timer = (REPO / "scripts" / "systemd" / "fc-commit.timer").read_text()
    t = re.search(r"^OnCalendar=\*-\*-\* (\d{2}):(\d{2}):\d{2} UTC$", timer, re.MULTILINE)
    assert t, "fc-commit.timer must fire at a fixed UTC time"
    start = int(t.group(1)) * 60 + int(t.group(2))
    wd = (REPO / ".github" / "workflows" / "watchdog.yml").read_text()
    c = re.search(r'"(\d+) (\d+) \* \* \*"\)\s*CHECK=commit', wd)
    assert c, "watchdog commit-check cron not found"
    watchdog = int(c.group(2)) * 60 + int(c.group(1))
    ends = (start + _const("MAX_MANIFEST_WAIT_MIN") + _const("BACKSTOP_ALLOW_MIN")
            + _const("RUN_BUDGET_S") // 60 + _const("OVERHEAD_ALLOW_MIN"))
    assert ends <= watchdog, f"commit phase can end at minute {ends}, watchdog looks at {watchdog}"
    assert _const("RUN_BUDGET_S") == 3600 and _const("MAX_MANIFEST_WAIT_MIN") == 30


@pytest.mark.parametrize("timeout, extra", [("3601", 0), ("1801", 1)])
def test_run_budget_is_enforced_before_any_run(box: Box, timeout: str, extra: int):
    for i in range(extra):
        _add_tier_s(box, f"second-{i}")
    r = box.run(TIMEOUT=timeout)
    assert r.returncode == 1, r.stdout + r.stderr
    _one_alert_containing(box, "run budget")
    assert "uv run fc run " not in box.calls_text()
    assert ops_evidence.job_record_ok(box.job("commit"), SEASON, DAY, "commit")[0] is False


def test_timeout_that_is_not_a_number_is_refused(box: Box):
    r = box.run(TIMEOUT="abc")
    assert r.returncode == 1
    assert "uv run fc run " not in box.calls_text()
    assert len(box.sink.posts) == 1


@pytest.mark.parametrize("value", ["31", "abc"])
def test_manifest_wait_above_the_cap_is_refused(box: Box, value: str):
    r = box.run(FC_MANIFEST_WAIT_MIN=value)
    assert r.returncode == 1
    _one_alert_containing(box, "FC_MANIFEST_WAIT_MIN")
    assert "uv run fc run " not in box.calls_text()


def test_injected_failure_alerts_before_any_git_or_network_action(box: Box):
    r = box.run(FC_INJECT_FAILURE="1")
    assert r.returncode == 1
    _one_alert_containing(box, "INJECTED")
    calls = box.calls_text()
    assert "uv run fc " not in calls and "docker" not in calls and "systemctl" not in calls
    assert box.remote_subjects() == ["seed"]
    assert _job(box)["ok"] is False


def test_injected_failure_alerts_even_before_the_season_starts(box: Box):
    r = box.run("2026-11-16", FC_INJECT_FAILURE="1")
    assert r.returncode == 1
    _one_alert_containing(box, "INJECTED")


@pytest.mark.parametrize("day", ["2026-11-23", "2027-04-01"])
@pytest.mark.parametrize("mode", ["commit", "reveal"])
def test_days_outside_the_season_exit_quietly(box: Box, day: str, mode: str):
    r = box.run(day, mode=mode)
    assert r.returncode == 0, r.stdout + r.stderr
    assert box.sink.posts == []
    assert "outside season" in r.stdout
    assert not (box.work / "runs").exists(), "a quiet day must leave no job record or log"
    assert box.calls_text() == ""


def test_default_season_is_the_rolling_season(box: Box):
    r = box.run("2026-11-23", SEASON="")  # empty = unset for ${SEASON:-default}
    assert r.returncode == 0
    assert f"outside season {SEASON} " in r.stdout


@pytest.mark.parametrize("bad_day", ["../etc", "2026-13-xx", "tomorrow"])
def test_malformed_day_is_refused_before_touching_the_disk(box: Box, bad_day: str):
    r = box.run(bad_day)
    assert r.returncode == 2
    assert not (box.work / "runs").exists()


def test_unreachable_remote_at_the_start_is_loud(box: Box):
    git(box.work, "remote", "set-url", "origin", "/nonexistent/remote.git")
    r = box.run()
    assert r.returncode == 1
    _one_alert_containing(box, "git pull failed")


def test_push_failure_retries_once_then_alerts_and_records(box: Box):
    r = box.run(FAKE_BREAK_REMOTE="1")
    assert r.returncode == 1, r.stdout + r.stderr
    _one_alert_containing(box, "push failed")
    assert "retrying once after git pull --rebase" in box.log_text()
    rec = _job(box)
    assert rec["ok"] is False and any("push" in f for f in rec["failures"])
    assert box.remote_subjects() == ["seed"]


def test_push_retry_rebases_over_a_concurrent_push_with_a_dirty_tree(box: Box):
    # Someone else pushes while the commit phase runs, and a tracked file is locally modified
    # (the previous reveal appends to its own log after committing it): the retry must cope.
    (box.work / "model-metadata" / "other-h.yml").write_text("model_id: other-h\ntier: H\n# dirty\n")
    r = box.run(FAKE_ADVANCE_REMOTE="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert box.sink.posts == []
    subjects = box.remote_subjects()
    assert "elsewhere" in subjects
    assert f"commit({SEASON}): {DAY} tier S commitments rooted" in subjects
    assert "retrying once after git pull --rebase" in box.log_text()


def test_missing_manifest_with_failing_packet_build_is_loud(box: Box):
    _retire_manifest(box)
    r = box.run(FAKE_PACKET_RC="1")
    assert r.returncode == 1
    _one_alert_containing(box, "packet build failed")
    assert "uv run fc run " not in box.calls_text()


def test_missing_manifest_with_failing_round_open_is_loud(box: Box):
    _retire_manifest(box)
    r = box.run(FAKE_ROUND_OPEN_RC="1")
    assert r.returncode == 1
    _one_alert_containing(box, "round open failed")


def test_backstop_round_is_pushed_before_the_first_run(box: Box):
    _retire_manifest(box)
    r = box.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert box.sink.posts == []
    backstop = f"round({SEASON}): open {DAY} (box backstop)"
    assert backstop in box.remote_seen.read_text(), "pushed only after the first fc run"
    assert backstop in box.remote_subjects()
    assert f"rounds/{SEASON}/{DAY}/manifest.json" in box.remote_files()


def test_manifest_arriving_while_waiting_prevents_the_backstop(box: Box):
    _retire_manifest(box)

    def publish_later():
        import time
        time.sleep(2)
        other = box.root / "publisher"
        subprocess.run(["git", "clone", "-q", str(box.remote), str(other)], check=True)
        d = other / "rounds" / SEASON / DAY
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text(json.dumps(
            {"round_id": f"{SEASON}-{DAY}", "T_commit": FAR_FUTURE_COMMIT,
             "T_reveal": FAR_FUTURE_REVEAL}))
        for c in (["add", "-A"], ["-c", "user.name=w", "-c", "user.email=w@e.invalid", "commit",
                                   "-qm", "workflow opens the round"], ["push", "-q", "origin", "main"]):
            subprocess.run(["git", *c], cwd=other, check=True)

    t = threading.Thread(target=publish_later)
    t.start()
    r = box.run(FC_MANIFEST_WAIT_MIN="1", FC_MANIFEST_POLL_S="1")
    t.join()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "uv run fc round open" not in box.calls_text()
    assert box.sink.posts == []


def test_a_commit_after_t_commit_is_refused(box: Box):
    r = box.run(FC_NOW="2099-01-01T12:00:01Z")
    assert r.returncode == 1
    _one_alert_containing(box, "past T_commit")
    assert "FC_NOW" in box.log_text()  # the clock override is never silent
    assert "uv run fc run " not in box.calls_text()


def test_not_enough_free_vram_is_loud(box: Box):
    r = box.run(FAKE_FREE_MIB="4000")
    assert r.returncode == 1
    _one_alert_containing(box, "insufficient free VRAM")
    assert "uv run fc run " not in box.calls_text()


def test_docker_down_is_loud(box: Box):
    r = box.run(FAKE_DOCKER_RC="1")
    assert r.returncode == 1
    _one_alert_containing(box, "docker unavailable")


@pytest.mark.parametrize("env", [{"FAKE_RUN_RC": "1"}, {"FAKE_FREE_MIB": "4000"}, {}])
def test_stopped_units_are_started_again_also_on_failure(box: Box, env: dict[str, str]):
    r = box.run(FC_STOP_UNITS="unit-a.service unit-b.timer", **env)
    assert r.returncode == (0 if not env else 1)
    lines = box.calls_text().splitlines()
    stops = [i for i, line in enumerate(lines) if line.startswith("systemctl --user stop")]
    starts = [i for i, line in enumerate(lines) if line.startswith("systemctl --user start")]
    assert [lines[i].split()[-1] for i in stops] == ["unit-a.service", "unit-b.timer"]
    assert sorted(lines[i].split()[-1] for i in starts) == ["unit-a.service", "unit-b.timer"]
    assert max(stops) < min(starts)
    runs = [i for i, line in enumerate(lines) if line.startswith("uv run fc run ")]
    assert all(max(stops) < i < min(starts) for i in runs)


def test_low_parse_rate_alerts_but_the_commitment_is_still_made(box: Box):
    r = box.run(FAKE_PARSE_RATE="0.5")
    assert r.returncode == 1, r.stdout + r.stderr
    _one_alert_containing(box, "parse rate")
    assert f"sidecar/house-test/{SEASON}-{DAY}.json" in box.remote_files()
    rec = _job(box)
    assert rec["ok"] is False and any("parse rate" in f for f in rec["failures"])
    assert ops_evidence.job_record_ok(box.job("commit"), SEASON, DAY, "commit")[0] is False


def test_parse_rate_floor_is_inclusive_and_overridable(box: Box):
    r = box.run(FAKE_PARSE_RATE="0.80")
    assert r.returncode == 0, r.stdout + r.stderr
    assert ops_evidence.job_record_ok(box.job("commit"), SEASON, DAY, "commit")[0] is True


def test_parse_rate_floor_can_be_lowered_by_the_operator(box: Box):
    r = box.run(FAKE_PARSE_RATE="0.5", FC_MIN_PARSE_RATE="0.4")
    assert r.returncode == 0, r.stdout + r.stderr


def test_no_tier_s_entries_is_loud(box: Box):
    (box.work / "model-metadata" / "house-test.yml").unlink()
    r = box.run()
    assert r.returncode == 1
    _one_alert_containing(box, "no Tier S entries")


def test_missing_signing_key_is_loud(box: Box):
    (box.home / "keys" / "runner.pem").unlink()
    r = box.run()
    assert r.returncode == 1
    _one_alert_containing(box, "no signing key")


@pytest.mark.parametrize("env, needle", [({"FAKE_COMMIT_RC": "1"}, "commit failed"),
                                         ({"FAKE_SUBMIT_RC": "1"}, "submit failed")])
def test_commit_and_submit_failures_are_loud(box: Box, env: dict[str, str], needle: str):
    r = box.run(**env)
    assert r.returncode == 1
    _one_alert_containing(box, needle)


def test_anchoring_trouble_is_a_warning_never_a_failure(box: Box):
    r = box.run(FAKE_ANCHOR_RC="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert box.sink.posts == []
    assert "WARN: OTS stamp did not complete" in box.log_text()
    calls = box.calls_text()
    assert f"uv run python -m fc.anchor stamp --round-dir rounds/{SEASON}/{DAY}" in calls


def test_reveal_publishes_outputs_and_both_job_records(box: Box):
    assert box.run().returncode == 0
    r = box.run(mode="reveal", FC_NOW=AFTER_REVEAL)
    assert r.returncode == 0, r.stdout + r.stderr
    assert box.sink.posts == []
    files = box.remote_files()
    assert f"model-output/house-test/{SEASON}-{DAY}.csv" in files
    assert f"runs/{SEASON}/{DAY}/house-test/transcript.jsonl" in files
    assert f"runs/{SEASON}/{DAY}/job-commit.json" in files
    assert f"runs/{SEASON}/{DAY}/job-reveal.json" in files
    assert f"reveal({SEASON}): {DAY} tier S outputs + reveal report" in box.remote_subjects()
    for mode in ("commit", "reveal"):
        assert ops_evidence.job_record_ok(box.job(mode), SEASON, DAY, mode) == (True, "ok")
    published = git(box.remote, "show", f"main:runs/{SEASON}/{DAY}/job-reveal.json")
    assert json.loads(published)["ok"] is True
    for stream in (r.stdout, r.stderr, box.log_text()):
        assert TOKEN not in stream and str(box.home) not in stream


def test_reveal_is_refused_when_far_too_early(box: Box):
    r = box.run(mode="reveal", FC_NOW="2098-12-31T00:00:00Z")
    assert r.returncode == 1
    _one_alert_containing(box, "too early for reveal")


def test_close_reveals_failure_is_loud_but_still_publishes(box: Box):
    r = box.run(mode="reveal", FC_NOW=AFTER_REVEAL, FAKE_CLOSE_RC="1")
    assert r.returncode == 1
    _one_alert_containing(box, "close-reveals failed")
    assert ops_evidence.job_record_ok(box.job("reveal"), SEASON, DAY, "reveal")[0] is False
    published = git(box.remote, "show", f"main:runs/{SEASON}/{DAY}/job-reveal.json")
    assert json.loads(published)["ok"] is False


def test_reveal_with_unreachable_remote_rewrites_the_record_as_failed(box: Box):
    r = box.run(mode="reveal", FC_NOW=AFTER_REVEAL, FAKE_BREAK_REMOTE="1")
    assert r.returncode == 1, r.stdout + r.stderr
    _one_alert_containing(box, "push failed")
    rec = _job(box, "reveal")
    assert rec["ok"] is False and any("push" in f for f in rec["failures"])
    assert ops_evidence.job_record_ok(box.job("reveal"), SEASON, DAY, "reveal")[0] is False


def test_unset_webhook_still_fails_and_says_the_alert_was_not_delivered(box: Box):
    r = box.run(webhook=False, FAKE_RUN_RC="1")
    assert r.returncode == 1
    assert "ALERT NOT DELIVERED" in box.log_text()
    assert box.sink.posts == []


def test_failed_delivery_falls_back_without_leaking_the_url(box: Box):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    r = box.run(FAKE_RUN_RC="1", ALERT_WEBHOOK=f"http://127.0.0.1:{port}/hook/{TOKEN}")
    assert r.returncode == 1
    assert "ALERT NOT DELIVERED" in box.log_text()
    for stream in (r.stdout, r.stderr, box.log_text()):
        assert TOKEN not in stream and socket.gethostname() not in stream


def test_no_error_swallowing_idioms_remain():
    code = re.sub(r"(?m)^\s*#.*$", "", SCRIPT.read_text())
    assert not re.search(r"\|\|\s*true", code)
    assert not re.search(r"\|\|\s*:\s*($|;)", code)
    assert "python3 -c \"import yaml" not in code
