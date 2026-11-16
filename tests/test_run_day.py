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
if cmd[:2] == ["service", "submit"]:
    if env.get("FAKE_BREAK_REMOTE"):
        subprocess.run(["git", "remote", "set-url", "origin", "/nonexistent/remote.git"],
                       check=True)
    rc = int(env.get("FAKE_SUBMIT_RC", "0"))
    if rc == 0:
        write(Path(opt("--round-dir")) / "roots" / "0.json", json.dumps({"root": "ab"}))
    sys.exit(rc)
if cmd[:2] == ["service", "close-reveals"]:
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
        return subprocess.run(["bash", "scripts/run_day.sh", day], cwd=self.work, env=e,
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
