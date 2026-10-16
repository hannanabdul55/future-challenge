"""FREEZE-01/03/04: identity records are deterministic, gated, and verifiable offline."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from fc import model_identity as M

SHA = "a" * 40


def _sib(name, size=None, sha=None, blob="b" * 40):
    lfs = {"sha256": sha, "size": size, "pointer_size": 134} if sha else None
    return SimpleNamespace(rfilename=name, size=size, blob_id=blob, lfs=lfs)


class FakeApi:
    def __init__(self, files, *, licence="apache-2.0", sha=SHA, with_commits=True):
        self.files, self.licence, self.sha, self.with_commits = files, licence, sha, with_commits
        self.calls = []

    def model_info(self, repo_id, *, revision=None, files_metadata=False):
        self.calls.append(("model_info", repo_id, revision, files_metadata))
        return SimpleNamespace(sha=self.sha, siblings=self.files,
                               card_data={"license": self.licence}, gated=False)

    def list_repo_commits(self, repo_id, *, revision=None):
        if not self.with_commits:
            raise RuntimeError("commits unavailable")
        return [SimpleNamespace(commit_id=self.sha,
                                created_at=datetime(2026, 10, 18, 9, 12, 44, tzinfo=UTC))]


GGUF = _sib("model-Q4_K_M.gguf", size=4_900_000_000, sha="c" * 64)
CFG = _sib("config.json", size=912)


def test_resolve_is_deterministic_and_pins_everything():
    api = FakeApi([CFG, GGUF])
    a = M.resolve("org/model", "main", api=api, prompt_template="You are a forecaster.")
    b = M.resolve("org/model", "main", api=api, prompt_template="You are a forecaster.")
    assert a.hash() == b.hash()
    rec = a.to_dict()
    assert rec["revision"] == SHA
    assert rec["hf_commit_time"] == "2026-10-18T09:12:44Z"
    assert [f["path"] for f in rec["files"]] == ["config.json", "model-Q4_K_M.gguf"]  # sorted
    assert rec["files"][1]["sha256"] == "c" * 64
    assert rec["inference"]["weights_file"] == "model-Q4_K_M.gguf"
    assert rec["identity_hash"] == M.identity_hash(rec)
    assert api.calls[0][3] is True  # files_metadata requested


def test_prompt_template_changes_the_hash():
    api = FakeApi([CFG, GGUF])
    a = M.resolve("org/model", "main", api=api, prompt_template="A")
    b = M.resolve("org/model", "main", api=api, prompt_template="B")
    assert a.hash() != b.hash()


def test_commit_time_failure_is_not_fatal():
    api = FakeApi([CFG, GGUF], with_commits=False)
    ident = M.resolve("org/model", "main", api=api)
    assert ident.hf_commit_time is None


def test_short_or_missing_sha_is_rejected():
    api = FakeApi([CFG, GGUF], sha="abc123")
    with pytest.raises(ValueError, match="no commit sha"):
        M.resolve("org/model", "main", api=api)


def test_pickled_weights_are_refused():
    api = FakeApi([CFG, _sib("pytorch_model.bin", size=10, sha="d" * 64)])
    with pytest.raises(ValueError, match="pickled"):
        M.resolve("org/model", "main", api=api)


def test_tier_s_gate_budget_and_licence():
    api = FakeApi([CFG, GGUF])
    ident = M.resolve("org/model", "main", api=api)
    assert M.check_entry(ident, "S").ok
    assert not M.check_entry(ident, "S", budget_bytes=1_000_000).ok
    big = M.check_entry(ident, "S", budget_bytes=1_000_000)
    assert any("tier A" in e for e in big.errors)

    ident2 = M.resolve("org/model", "main", api=FakeApi([CFG, GGUF], licence="proprietary"))
    g = M.check_entry(ident2, "S")
    assert any("allowlist" in e for e in g.errors)

    # Tier A is not budget-gated.
    assert M.check_entry(ident, "A", budget_bytes=1_000_000).ok


def test_tier_s_requires_a_single_chosen_gguf():
    two = [CFG, GGUF, _sib("model-Q8_0.gguf", size=8_000_000_000, sha="e" * 64)]
    ident = M.resolve("org/model", "main", api=FakeApi(two))
    assert ident.inference.weights_file is None
    g = M.check_entry(ident, "S")
    assert any("weights_file" in e for e in g.errors)
    ident.inference.weights_file = "model-Q4_K_M.gguf"
    assert M.check_entry(ident, "S").ok


def test_verify_snapshot_detects_tampering(tmp_path: Path):
    data = b"gguf-bytes" * 100
    sha = hashlib.sha256(data).hexdigest()
    api = FakeApi([CFG, _sib("m.gguf", size=len(data), sha=sha)])
    rec = M.resolve("org/model", "main", api=api).to_dict()
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "m.gguf").write_bytes(data)
    (snap / "config.json").write_bytes(b"x" * 912)
    assert M.verify_snapshot(rec, snap) == []
    (snap / "m.gguf").write_bytes(data[:-1] + b"!")
    assert M.verify_snapshot(rec, snap) == ["sha256 mismatch m.gguf"]
    (snap / "m.gguf").unlink()
    assert M.verify_snapshot(rec, snap, only=["m.gguf"]) == ["missing m.gguf"]


def test_metadata_roundtrip_and_tamper_detection(tmp_path: Path):
    rec = M.resolve("org/model", "main", api=FakeApi([CFG, GGUF])).to_dict()
    p = tmp_path / "acme-v1.yml"
    p.write_text(yaml.safe_dump({"model_id": "acme-v1", "tier": "S", "model_identity": rec}))
    loaded = M.load_identity_record(p)
    assert loaded["identity_hash"] == rec["identity_hash"]
    assert M.identity_bytes(loaded) == M.identity_bytes(rec)

    tampered = dict(rec, revision="f" * 40)
    p.write_text(yaml.safe_dump({"model_id": "acme-v1", "tier": "S", "model_identity": tampered}))
    with pytest.raises(ValueError, match="identity_hash"):
        M.load_identity_record(p)
