"""Webhook alert for the daily job, the watchdog and the OnFailure unit (D-05, D-15).

    ALERT_WEBHOOK=https://ntfy.sh/my-topic python -m fc.alert --title T --message M [--log-tail FILE]

Standard library only: the GitHub watchdog and the box's fc-alert@.service call this with the
system ``python3`` before any dependency is installed.

The body is plain text by default (ntfy and generic endpoints read it; the Title/Priority/Tags
headers are ntfy's and ignored elsewhere). Discord and Slack reject plain text, so their hosts
get the minimal JSON they require. The format is detected from the URL host and can be forced
with ``ALERT_FORMAT=text|discord|slack``.

The delivered payload is prefixed with this machine's host name. Nothing this module prints
contains the host name or the webhook URL: logs written by the daily job become public at reveal.
Exit codes: 0 delivered, 2 ALERT_WEBHOOK unset, 3 delivery failed.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Callable, Sequence

MAX_TEXT = 3500
MAX_DISCORD = 1900
DEFAULT_TAIL_LINES = 25
FORMATS = ("text", "discord", "slack")

HttpPost = Callable[[str, bytes, dict, float], int]


class AlertError(Exception):
    """Delivery failed; the message is already scrubbed of the URL and the host name."""


def detect_format(url: str, override: str | None = None) -> str:
    """discord for discord.com webhooks, slack for hooks.slack.com, text otherwise."""
    if override in FORMATS:
        return override
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host in ("discord.com", "discordapp.com") or host.endswith((".discord.com",
                                                                    ".discordapp.com")):
        return "discord"
    if host == "hooks.slack.com":
        return "slack"
    return "text"


def _ascii(text: str) -> str:
    return text.encode("ascii", "replace").decode("ascii").replace("\r", " ").replace("\n", " ")


def build_request(url: str, message: str, title: str, fmt: str) -> tuple[bytes, dict]:
    """Return (body, headers) for one POST. ``url`` is accepted for symmetry and unused."""
    if fmt == "discord":
        content = f"**{title}**\n{message}"[:MAX_DISCORD]
        return json.dumps({"content": content}).encode(), {"Content-Type": "application/json"}
    if fmt == "slack":
        text = f"*{title}*\n{message}"[:MAX_TEXT]
        return json.dumps({"text": text}).encode(), {"Content-Type": "application/json"}
    body = message[:MAX_TEXT].encode("utf-8")
    while len(body) > MAX_TEXT:  # multi-byte characters can push the encoded form over
        message = message[:-1]
        body = message.encode("utf-8")
    headers = {"Content-Type": "text/plain; charset=utf-8", "Title": _ascii(title),
               "Priority": "high", "Tags": "warning"}
    return body, headers


def _scrub(text: str, url: str | None) -> str:
    """Replace the webhook URL, its path and its query, and the host name, with <redacted>."""
    out = str(text)
    secrets_ = []
    if url:
        parts = urllib.parse.urlsplit(url)
        secrets_ += [url, parts.path, parts.query]
        secrets_ += [seg for seg in parts.path.split("/") if len(seg) >= 6]
    secrets_.append(socket.gethostname())
    for s in sorted({s for s in secrets_ if s and s != "/"}, key=len, reverse=True):
        out = out.replace(s, "<redacted>")
    return out


def _urllib_post(url: str, body: bytes, headers: dict, timeout: float) -> int:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return int(resp.status)


def send_alert(message: str, *, title: str = "fc alert", url: str | None = None,
               http_post: HttpPost | None = None, timeout: float = 20,
               fmt: str | None = None) -> int:
    """POST one alert; return the HTTP status. Raises AlertError with a scrubbed reason."""
    url = url if url is not None else os.environ.get("ALERT_WEBHOOK", "")
    if not url:
        raise AlertError("ALERT_WEBHOOK not set")
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise AlertError("unsupported webhook URL scheme (need http or https)")
    chosen = detect_format(url, fmt if fmt is not None else os.environ.get("ALERT_FORMAT"))
    body, headers = build_request(url, f"[{socket.gethostname()}] {message}", title, chosen)
    post = http_post or _urllib_post
    try:
        status = post(url, body, headers, timeout)
    except urllib.error.HTTPError as e:
        raise AlertError(f"HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise AlertError(_scrub(f"{type(e).__name__}: {e.reason}", url)) from None
    except (OSError, ValueError) as e:
        raise AlertError(_scrub(f"{type(e).__name__}: {e}", url)) from None
    if not 200 <= status < 300:
        raise AlertError(f"HTTP {status}")
    return status


def _tail(path: str, lines: int) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return "".join(deque(fh, maxlen=max(lines, 0)))
    except OSError:
        return ""


def main(argv: Sequence[str] | None = None, *, http_post: HttpPost | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fc.alert", description=__doc__.split("\n")[0])
    ap.add_argument("--message", required=True)
    ap.add_argument("--title", default="fc alert")
    ap.add_argument("--log-tail", metavar="FILE", help="append the last lines of this file")
    ap.add_argument("--lines", type=int, default=DEFAULT_TAIL_LINES)
    args = ap.parse_args(argv)

    message = args.message
    if args.log_tail:
        tail = _tail(args.log_tail, args.lines)
        if tail.strip():
            message = f"{message}\n--- log tail ---\n{tail.rstrip()}"

    url = os.environ.get("ALERT_WEBHOOK", "")
    if not url:
        first = args.message.splitlines()[0] if args.message.splitlines() else ""
        print(f"ALERT_WEBHOOK not set; alert NOT delivered: {first}", file=sys.stderr)
        return 2
    try:
        status = send_alert(message, title=args.title, url=url, http_post=http_post)
    except AlertError as e:
        print(f"alert delivery failed: {_scrub(str(e), url)}", file=sys.stderr)
        return 3
    print(f"alert delivered (HTTP {status})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
