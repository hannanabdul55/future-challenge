"""ENTRY-02/03, FREEZE-04: the registration gate CI runs on model-metadata PRs."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from fc import commit as C
from fc import freeze as F
from fc import model_identity as M


class Api:
    def __init__(self, sha="4" * 40, file_sha="c" * 64, licence="apache-2.0"):
        self.sha, self.file_sha, self.licence = sha, file_sha, licence

    def model_info(self, repo_id, *, revision=None, files_metadata=False):
        sib = SimpleNamespace(rfilename="m.gguf", size=3_000_000_000, blob_id="b" * 40,
                              lfs={"sha256": self.file_sha, "size": 3_000_000_000, "pointer_size": 1})
        return SimpleNamespace(sha=self.sha, siblings=[sib], card_data={"license": self.licence},
                               gated=False)

    def list_repo_commits(self, repo_id, *, revision=None):
        return []


def _entry(tmp_path: Path, model_id="acme-a", tier="S", primary=True, api=None) -> Path:
    _, pub = C.generate_keypair()
    ident = M.resolve("org/m", "main", api=api or Api())
    meta = tmp_path / "model-metadata" / f"{model_id}.yml"
    F.write_entry_yaml(meta, model_id=model_id, team="Acme", tier=tier, identity=ident,
                       public_key_pem=pub.decode(), primary=primary)
    return meta


def test_gate_passes_a_clean_entry_and_resolves(tmp_path):
    meta = _entry(tmp_path)
    assert F.check_entry_file(meta).ok
    assert F.check_entry_file(meta, resolve=True, api=Api()).ok
    assert F.check_team_limits(tmp_path / "model-metadata") == []


def test_gate_catches_hub_drift_and_tampering(tmp_path):
    meta = _entry(tmp_path)
    g = F.check_entry_file(meta, resolve=True, api=Api(file_sha="d" * 64))
    assert any("file hashes differ" in e for e in g.errors)
    g = F.check_entry_file(meta, resolve=True, api=Api(sha="5" * 40))
    assert any("different commit sha" in e for e in g.errors)
    doc = yaml.safe_load(meta.read_text())
    doc["model_identity"]["revision"] = "6" * 40
    meta.write_text(yaml.safe_dump(doc))
    assert any("identity_hash" in e for e in F.check_entry_file(meta).errors)


def test_gate_enforces_tier_rules_and_key(tmp_path):
    meta = _entry(tmp_path, api=Api(licence="proprietary"))
    assert any("allowlist" in e for e in F.check_entry_file(meta).errors)
    doc = yaml.safe_load(meta.read_text())
    doc["public_key_pem"] = "not a key"
    meta.write_text(yaml.safe_dump(doc))
    assert any("public_key_pem" in e for e in F.check_entry_file(meta).errors)

    api_meta = tmp_path / "model-metadata" / "acme-api.yml"
    api_meta.write_text(yaml.safe_dump({"model_id": "acme-api", "tier": "S", "primary": False,
                                        "public_key_pem": doc["public_key_pem"],
                                        "model_identity": {"type": "api", "provider": "x",
                                                           "model_id": "y"}}))
    errs = F.check_entry_file(api_meta).errors
    assert any("capped at tier A" in e for e in errs) and any("fingerprint" in e for e in errs)


def test_team_limits(tmp_path):
    for i in range(6):
        _entry(tmp_path, model_id=f"acme-{i}", primary=(i == 0))
    errs = F.check_team_limits(tmp_path / "model-metadata")
    assert any("6 entries" in e for e in errs)
    _entry(tmp_path, model_id="zed-1", primary=False)
    assert any("team zed: 0 primary" in e for e in F.check_team_limits(tmp_path / "model-metadata"))
