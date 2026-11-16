"""fc keygen creates private keys owner-only and never overwrites one (T-06-28)."""

from __future__ import annotations

import os
import stat
import sys

import pytest

from fc import cli

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@posix_only
def test_keygen_creates_private_key_0600_in_a_0700_directory(tmp_path):
    out = tmp_path / "keys"
    cli.main(["keygen", "--out", str(out)])
    assert _mode(out) == 0o700
    assert _mode(out / "private.pem") == 0o600
    assert _mode(out / "public.pem") == 0o644
    assert b"PRIVATE KEY" in (out / "private.pem").read_bytes()
    assert b"PUBLIC KEY" in (out / "public.pem").read_bytes()


def test_keygen_refuses_to_overwrite_an_existing_private_key(tmp_path, capsys):
    out = tmp_path / "keys"
    cli.main(["keygen", "--out", str(out)])
    before = (out / "private.pem").read_bytes()
    public_before = (out / "public.pem").read_bytes()

    with pytest.raises(SystemExit) as exc:
        cli.main(["keygen", "--out", str(out)])

    assert exc.value.code == 1
    assert "refusing to overwrite" in capsys.readouterr().err
    assert (out / "private.pem").read_bytes() == before
    assert (out / "public.pem").read_bytes() == public_before
