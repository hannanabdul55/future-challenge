"""Static checks and installer tests for the GPU box's systemd user units (D-06).

Unit files are parsed line by line (configparser cannot hold repeated Environment= keys).
The installer is exercised only with --dry-run or with fake loginctl/systemctl/systemd-analyze
on PATH and HOME pointed at a tmp dir: nothing here touches a real systemd.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

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


# --- reveal phase ----------------------------------------------------------------------


def test_reveal_timer_is_1800_utc_and_persistent():
    t = parse_unit(UNITS / "fc-reveal.timer")
    assert one(t, "Timer.OnCalendar") == "*-*-* 18:00:00 UTC"
    assert one(t, "Timer.Persistent") == "true"
    assert one(t, "Timer.Unit") == "fc-reveal.service"
    assert one(t, "Install.WantedBy") == "timers.target"


def test_reveal_service_wiring():
    u = parse_unit(UNITS / "fc-reveal.service")
    assert one(u, "Unit.OnFailure") == "fc-alert@%n.service"
    assert one(u, "Service.Type") == "oneshot"
    assert one(u, "Service.EnvironmentFile") == "%h/.config/fc/env"
    assert "MODE=reveal" in u["Service.Environment"]
    assert one(u, "Service.ExecStart").endswith("scripts/run_day.sh")
    assert one(u, "Service.TimeoutStartSec") == "2h"


def test_both_phases_fail_into_the_alert_template():
    for name in ("fc-commit.service", "fc-reveal.service"):
        assert "OnFailure=fc-alert@%n.service" in (UNITS / name).read_text()


def test_dry_run_renders_all_five_files(tmp_path):
    assert dry_run(tmp_path).returncode == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "fc-alert@.service",
        "fc-commit.service",
        "fc-commit.timer",
        "fc-reveal.service",
        "fc-reveal.timer",
    ]


def test_installer_never_uses_sudo():
    assert "sudo" not in INSTALL.read_text()


# --- live install path, with fakes -----------------------------------------------------


class Box:
    """A fake host: tmp HOME, fake loginctl/systemd-analyze/systemctl logging their argv."""

    def __init__(self, tmp_path: Path):
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.log = tmp_path / "argv.log"
        self.env_file = self.home / ".config" / "fc" / "env"
        self.user_dir = self.home / ".config" / "systemd" / "user"
        script = '#!/bin/sh\necho "{n} $*" >> "' + str(self.log) + '"\n{extra}\n'
        extras = {
            "loginctl": 'echo "Linger=${FAKE_LINGER:-yes}"',
            "systemd-analyze": 'exit "${FAKE_ANALYZE_RC:-0}"',
            "systemctl": "exit 0",
        }
        for tool, extra in extras.items():
            f = self.bin / tool
            f.write_text(script.format(n=tool, extra=extra))
            f.chmod(0o755)

    def write_env(self, mode: int = 0o600) -> None:
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        self.env_file.write_text("ALERT_WEBHOOK=placeholder\n")
        self.env_file.chmod(mode)

    def run(self, **extra: str) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "HOME": str(self.home),
            "USER": "tester",
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            **extra,
        }
        return subprocess.run(
            ["bash", str(INSTALL)], capture_output=True, text=True, env=env, cwd=ROOT, check=False
        )

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []


@pytest.fixture
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


def test_refuses_without_linger(box: Box):
    box.write_env()
    res = box.run(FAKE_LINGER="no")
    assert res.returncode != 0
    assert "linger" in (res.stderr + res.stdout).lower()
    assert not any(c.startswith("systemctl") for c in box.calls())
    assert not box.user_dir.exists()


def test_refuses_a_missing_env_file(box: Box):
    res = box.run()
    assert res.returncode != 0
    assert "600" in res.stderr + res.stdout
    assert not any(c.startswith("systemctl") for c in box.calls())


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o700, 0o666])
def test_refuses_an_env_file_that_is_not_mode_600(box: Box, mode: int):
    box.write_env(mode)
    res = box.run()
    assert res.returncode != 0
    assert "600" in res.stderr + res.stdout
    assert not any(c.startswith("systemctl") for c in box.calls())
    assert not box.user_dir.exists()


def test_good_state_installs_verifies_then_enables(box: Box):
    box.write_env()
    res = box.run()
    assert res.returncode == 0, res.stderr
    for name in (
        "fc-alert@.service",
        "fc-commit.service",
        "fc-commit.timer",
        "fc-reveal.service",
        "fc-reveal.timer",
    ):
        text = (box.user_dir / name).read_text()
        assert "@FC_ROOT@" not in text
    assert str(ROOT) in (box.user_dir / "fc-commit.service").read_text()
    calls = box.calls()
    verify = next(i for i, c in enumerate(calls) if c.startswith("systemd-analyze --user verify"))
    reload_ = calls.index("systemctl --user daemon-reload")
    enable = calls.index("systemctl --user enable --now fc-commit.timer fc-reveal.timer")
    assert verify < reload_ < enable
    assert str(box.user_dir / "fc-commit.service") in calls[verify]
    assert str(box.user_dir / "fc-reveal.timer") in calls[verify]
    assert "systemctl --user list-timers fc-* --all" in calls
    assert calls.index("systemctl --user list-timers fc-* --all") > enable


def test_failing_verify_aborts_before_enable(box: Box):
    box.write_env()
    res = box.run(FAKE_ANALYZE_RC="1")
    assert res.returncode != 0
    calls = box.calls()
    assert any(c.startswith("systemd-analyze --user verify") for c in calls)
    assert not any("daemon-reload" in c or " enable " in c for c in calls)


def test_failing_verify_leaves_an_existing_install_untouched(box: Box):
    box.write_env()
    box.user_dir.mkdir(parents=True)
    old = box.user_dir / "fc-commit.service"
    old.write_text("# previous good unit\n")
    res = box.run(FAKE_ANALYZE_RC="1")
    assert res.returncode != 0
    assert old.read_text() == "# previous good unit\n"
    assert not (box.user_dir / "fc-reveal.timer").exists()


def test_refuses_a_checkout_path_systemd_cannot_hold(box: Box):
    box.write_env()
    res = box.run(FC_ROOT="/srv/has space/fc")
    assert res.returncode != 0
    assert not any(c.startswith("systemctl") for c in box.calls())


def test_env_file_mode_helper_matches_the_fixture(box: Box):
    box.write_env(0o600)
    assert stat.S_IMODE(box.env_file.stat().st_mode) == 0o600
