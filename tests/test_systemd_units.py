"""Static checks and installer tests for the GPU box's systemd user units (D-06).

Unit files are parsed line by line (configparser cannot hold repeated Environment= keys).
The installer is exercised only with --dry-run or with fake loginctl/systemctl/systemd-analyze
on PATH and HOME pointed at a tmp dir: nothing here touches a real systemd.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNITS = ROOT / "scripts" / "systemd"
INSTALL = UNITS / "install.sh"


def parse_unit(path: Path) -> dict[str, list[str]]:
    """Return {"Section.Key": [values...]} preserving repeated keys."""
    out: dict[str, list[str]] = {}
    section = ""
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        key, _, value = line.partition("=")
        out.setdefault(f"{section}.{key.strip()}", []).append(value.strip())
    return out


def one(unit: dict[str, list[str]], key: str) -> str:
    assert key in unit, f"missing {key}"
    assert len(unit[key]) == 1, f"{key} repeated: {unit[key]}"
    return unit[key][0]


def dry_run(dest: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(INSTALL), "--dry-run", str(dest)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )


# --- commit phase (tracer) -------------------------------------------------------------


def test_commit_timer_is_0005_utc_and_persistent():
    t = parse_unit(UNITS / "fc-commit.timer")
    cal = one(t, "Timer.OnCalendar")
    assert cal == "*-*-* 00:05:00 UTC"
    assert one(t, "Timer.Persistent") == "true"
    assert one(t, "Timer.Unit") == "fc-commit.service"
    assert one(t, "Install.WantedBy") == "timers.target"


def test_commit_service_wiring():
    u = parse_unit(UNITS / "fc-commit.service")
    assert one(u, "Unit.OnFailure") == "fc-alert@%n.service"
    assert one(u, "Service.Type") == "oneshot"
    assert one(u, "Service.EnvironmentFile") == "%h/.config/fc/env"
    assert "MODE=commit" in u["Service.Environment"]
    assert one(u, "Service.ExecStart").endswith("scripts/run_day.sh")
    assert one(u, "Service.WorkingDirectory") == "@FC_ROOT@"
    assert one(u, "Service.TimeoutStartSec") == "11h"
    path_env = [v for v in u["Service.Environment"] if v.startswith("PATH=")]
    assert len(path_env) == 1
    assert "%h/.local/bin" in path_env[0] and "/usr/lib/wsl/lib" in path_env[0]


def test_alert_template_runs_fc_alert_with_the_env_file():
    u = parse_unit(UNITS / "fc-alert@.service")
    assert one(u, "Service.Type") == "oneshot"
    assert one(u, "Service.EnvironmentFile") == "%h/.config/fc/env"
    exec_start = one(u, "Service.ExecStart")
    assert "python3 -m fc.alert" in exec_start
    assert "%i" in exec_start


def test_no_unit_inlines_a_webhook_or_url():
    for path in UNITS.glob("*.service"):
        assert "http" not in path.read_text(), path.name
    for path in UNITS.glob("*.timer"):
        assert "http" not in path.read_text(), path.name


def test_dry_run_renders_units_with_the_checkout_path(tmp_path):
    res = dry_run(tmp_path)
    assert res.returncode == 0, res.stderr
    rendered = sorted(p.name for p in tmp_path.iterdir())
    for name in ("fc-commit.service", "fc-commit.timer", "fc-alert@.service"):
        assert name in rendered
    for p in tmp_path.iterdir():
        text = p.read_text()
        assert "@FC_ROOT@" not in text, p.name
    assert str(ROOT) in (tmp_path / "fc-commit.service").read_text()
    assert str(ROOT) in (tmp_path / "fc-alert@.service").read_text()


def test_dry_run_does_not_call_systemctl(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "argv.log"
    for tool in ("systemctl", "systemd-analyze", "loginctl"):
        f = bin_dir / tool
        f.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{log}"\n')
        f.chmod(0o755)
    dest = tmp_path / "out"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    res = subprocess.run(
        ["bash", str(INSTALL), "--dry-run", str(dest)],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert not log.exists()
