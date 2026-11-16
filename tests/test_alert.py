"""fc.alert: webhook delivery for the daily job, the watchdog and the OnFailure unit (D-05, D-15).

Everything here talks to a throwaway local HTTP server; nothing leaves the machine.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from fc import alert

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "s3cr3t-token"


class _Sink:
    def __init__(self) -> None:
        self.posts: list[dict] = []
        sink = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(n)
                sink.posts.append({"path": self.path, "headers": dict(self.headers), "body": body})
                self.send_response(sink.status)
                self.end_headers()

            def log_message(self, *a):
                pass

        self.status = 200
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/hook/{TOKEN}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def sink():
    s = _Sink()
    try:
        yield s
    finally:
        s.close()


def run_cli(args: list[str], env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT), **env_extra}
    return subprocess.run([sys.executable, "-m", "fc.alert", *args], capture_output=True,
                          text=True, env=env, cwd=ROOT, check=False)


# --- format detection ----------------------------------------------------------------


def test_detect_format_by_host():
    assert alert.detect_format("https://discord.com/api/webhooks/1/abc") == "discord"
    assert alert.detect_format("https://canary.discord.com/api/webhooks/1/abc") == "discord"
    assert alert.detect_format("https://hooks.slack.com/services/T/B/x") == "slack"
    assert alert.detect_format("https://ntfy.sh/my-topic") == "text"
    assert alert.detect_format("http://127.0.0.1:9/hook") == "text"


def test_detect_format_is_not_fooled_by_lookalike_hosts():
    assert alert.detect_format("https://discord.com.evil.example/x") == "text"
    assert alert.detect_format("https://evil.example/discord.com") == "text"


def test_detect_format_override_wins():
    assert alert.detect_format("https://ntfy.sh/t", override="slack") == "slack"
    assert alert.detect_format("https://discord.com/api/webhooks/1/a", override="text") == "text"
    assert alert.detect_format("https://ntfy.sh/t", override="nonsense") == "text"


# --- payloads ------------------------------------------------------------------------


def test_text_request_carries_the_message_and_ntfy_headers():
    body, headers = alert.build_request("https://ntfy.sh/t", "disk is full", "fc alert", "text")
    assert body == b"disk is full"
    assert headers["Title"] == "fc alert"
    assert headers["Content-Type"].startswith("text/plain")


def test_discord_and_slack_requests_are_valid_json():
    body, headers = alert.build_request("u", "hello", "T", "discord")
    assert json.loads(body)["content"].endswith("hello")
    assert headers["Content-Type"] == "application/json"
    body, headers = alert.build_request("u", "hello", "T", "slack")
    assert json.loads(body)["text"].endswith("hello")


def test_discord_content_is_capped_below_the_2000_character_limit():
    body, _ = alert.build_request("u", "x" * 10000, "T", "discord")
    assert len(json.loads(body)["content"]) <= 1900


def test_non_ascii_title_never_breaks_the_header():
    _, headers = alert.build_request("u", "m", "café — fail", "text")
    headers["Title"].encode("ascii")


# --- delivery ------------------------------------------------------------------------


def test_send_alert_posts_text_with_host_prefix(sink):
    code = alert.send_alert("run failed for house-test", title="fc run_day FAILED", url=sink.url)
    assert code == 200
    (post,) = sink.posts
    assert post["path"] == f"/hook/{TOKEN}"
    text = post["body"].decode()
    assert text.startswith(f"[{socket.gethostname()}] ")
    assert "run failed for house-test" in text
    assert post["headers"]["Title"] == "fc run_day FAILED"


def test_send_alert_caps_the_payload():
    seen = []

    def fake_post(url, body, headers, timeout):
        seen.append(body)
        return 200

    alert.send_alert("y" * 20000, url="http://127.0.0.1:9/hook", http_post=fake_post)
    assert len(seen[0]) <= 3500


def test_send_alert_refuses_non_http_schemes():
    with pytest.raises(alert.AlertError, match="scheme"):
        alert.send_alert("m", url="file:///etc/passwd")


def test_send_alert_non_2xx_is_an_error(sink):
    sink.status = 500
    with pytest.raises(alert.AlertError, match="HTTP 500"):
        alert.send_alert("m", url=sink.url)


def test_scrub_removes_the_url_its_path_and_its_query():
    url = f"https://example.invalid/hook/{TOKEN}?key=abc"
    out = alert._scrub(f"failed {url} path /hook/{TOKEN} q key=abc", url)
    assert TOKEN not in out and "key=abc" not in out and "example.invalid/hook" not in out
    assert "<redacted>" in out


# --- command line --------------------------------------------------------------------


def test_cli_delivers_and_reports_success(sink):
    r = run_cli(["--title", "T", "--message", "boom"], {"ALERT_WEBHOOK": sink.url})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "alert delivered (HTTP 200)"
    assert len(sink.posts) == 1
    assert b"boom" in sink.posts[0]["body"]


def test_cli_appends_the_log_tail(sink, tmp_path):
    log = tmp_path / "run.log"
    log.write_text("".join(f"line {i}\n" for i in range(100)))
    r = run_cli(["--message", "boom", "--log-tail", str(log), "--lines", "3"],
                {"ALERT_WEBHOOK": sink.url})
    assert r.returncode == 0, r.stderr
    text = sink.posts[0]["body"].decode()
    assert "line 99" in text and "line 97" in text and "line 96" not in text


def test_cli_missing_log_tail_file_is_not_an_error(sink, tmp_path):
    r = run_cli(["--message", "boom", "--log-tail", str(tmp_path / "nope.log")],
                {"ALERT_WEBHOOK": sink.url})
    assert r.returncode == 0, r.stderr


def test_cli_discord_format_from_override(sink):
    r = run_cli(["--message", "boom"], {"ALERT_WEBHOOK": sink.url, "ALERT_FORMAT": "discord"})
    assert r.returncode == 0, r.stderr
    assert json.loads(sink.posts[0]["body"])["content"].endswith("boom")


def test_cli_unset_webhook_exits_2_and_names_the_message():
    r = run_cli(["--message", "first line\nsecond line"], {})
    assert r.returncode == 2
    assert "ALERT_WEBHOOK not set" in r.stderr
    assert "first line" in r.stderr and "second line" not in r.stderr


def test_cli_delivery_failure_exits_3_and_leaks_nothing():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now
    url = f"http://127.0.0.1:{port}/hook/{TOKEN}"
    r = run_cli(["--message", "boom"], {"ALERT_WEBHOOK": url})
    assert r.returncode == 3
    assert "alert delivery failed" in r.stderr
    for stream in (r.stdout, r.stderr):
        assert TOKEN not in stream
        assert socket.gethostname() not in stream


def test_module_imports_only_the_standard_library():
    src = (ROOT / "fc" / "alert.py").read_text()
    for banned in ("requests", "yaml", "pandas", "numpy", "httpx"):
        assert f"import {banned}" not in src and f"from {banned}" not in src
