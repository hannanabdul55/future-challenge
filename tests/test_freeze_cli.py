"""FREEZE-04 / RUN-06: `fc freeze` can pin the context length and max tokens into the
identity, so a model that does not fit the hub GPU at 16k can be frozen with a smaller window."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from fc import cli
from fc import model_identity as M

WEIGHTS = b"GGUF" + b"\x00" * 4096
WSHA = hashlib.sha256(WEIGHTS).hexdigest()


class FakeApi:
    def model_info(self, repo_id, *, revision=None, files_metadata=False):
        sib = SimpleNamespace(rfilename="m.gguf", size=len(WEIGHTS), blob_id="b" * 40,
                              lfs={"sha256": WSHA, "size": len(WEIGHTS), "pointer_size": 134})
        return SimpleNamespace(sha="3" * 40, siblings=[sib], card_data={"license": "mit"},
                               gated=False)

    def list_repo_commits(self, repo_id, *, revision=None):
        return []


def _freeze(tmp_path: Path, monkeypatch, *extra: str) -> Path:
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "HfApi", FakeApi)
    out = tmp_path / "house-test.yml"
    with pytest.raises(SystemExit) as ex:
        cli.main(["freeze", "--repo", "org/m", "--model-id", "house-test", "--team", "House",
                  "--tier", "S", "--weights-file", "m.gguf", "--out", str(out), *extra])
    assert ex.value.code == 0
    return out


def test_freeze_pins_context_length_and_max_tokens(tmp_path, monkeypatch):
    out = _freeze(tmp_path, monkeypatch, "--context-length", "8192", "--max-tokens", "1024")
    rec = yaml.safe_load(out.read_text())["model_identity"]
    assert rec["inference"]["context_length"] == 8192
    assert rec["inference"]["decoding"]["max_tokens"] == 1024
    assert M.load_identity_record(out)["identity_hash"] == rec["identity_hash"]


def test_freeze_defaults_are_unchanged(tmp_path, monkeypatch):
    out = _freeze(tmp_path, monkeypatch)
    inf = yaml.safe_load(out.read_text())["model_identity"]["inference"]
    assert inf["context_length"] == 16384 and inf["decoding"]["max_tokens"] == 4096


@pytest.mark.parametrize("flags,needle", [
    (("--context-length", "4096", "--max-tokens", "4096"), "max-tokens"),
    (("--context-length", "4096", "--max-tokens", "8192"), "max-tokens"),
    (("--context-length", "1024", "--max-tokens", "128"), "context-length"),
    (("--max-tokens", "32"), "max-tokens"),
])
def test_freeze_rejects_impossible_windows(tmp_path, monkeypatch, capsys, flags, needle):
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "HfApi", FakeApi)
    out = tmp_path / "x.yml"
    with pytest.raises(SystemExit) as ex:
        cli.main(["freeze", "--repo", "org/m", "--model-id", "house-test", "--team", "House",
                  "--tier", "S", "--weights-file", "m.gguf", "--out", str(out), *flags])
    assert ex.value.code not in (0, None)
    assert needle in capsys.readouterr().err
    assert not out.exists()
