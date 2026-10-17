"""FREEZE-02/03/05: an identity hash goes through the ordinary commitment service and the
freeze is verified against a daily round's T_open, offline."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import yaml
from test_verify_cli import BLOCK_TIME_OK, _anchor_the_root

from fc import commit as C
from fc import freeze as F
from fc import model_identity as M
from fc import service as S

FREEZE_AT = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)  # inside the fixture round's window
SHA = "1" * 40


class FakeApi:
    def model_info(self, repo_id, *, revision=None, files_metadata=False):
        sib = SimpleNamespace(rfilename="m-Q4_K_M.gguf", size=4_000_000_000, blob_id="b" * 40,
                              lfs={"sha256": "c" * 64, "size": 4_000_000_000, "pointer_size": 134})
        cfg = SimpleNamespace(rfilename="config.json", size=10, blob_id="d" * 40, lfs=None)
        return SimpleNamespace(sha=SHA, siblings=[sib, cfg], card_data={"license": "apache-2.0"},
                               gated=False)

    def list_repo_commits(self, repo_id, *, revision=None):
        return []


def fr_round_id(fr) -> str:
    return fr.round_id


def _frozen_entry(fr, model_id="acme-v3") -> tuple[Path, Path]:
    ident = M.resolve("org/model", "main", api=FakeApi(), prompt_template="forecast")
    meta = fr.root / "model-metadata" / f"{model_id}.yml"
    F.write_entry_yaml(meta, model_id=model_id, team="Acme", tier="S", identity=ident,
                       public_key_pem=None)
    sidecar = fr.root / "sidecar" / model_id / f"{fr.round_id}.json"
    req, nonce = F.commit_identity(meta, season_round_id=fr.round_id, team_sk=fr.team_sk,
                                   sidecar_path=sidecar)
    receipt, outcome = S.submit_commitment(req, fr.team_pk, fr.hub_sk, fr.round_dir, now=FREEZE_AT)
    assert outcome == "accepted" and receipt is not None
    root_doc = json.loads((fr.round_dir / "roots" / "0.json").read_text())
    _anchor_the_root(fr.round_dir, 0, BLOCK_TIME_OK)  # anchored 2026-09-09T13:00Z
    C.write_sidecar(sidecar, receipt, C.merkle_proof([req.commit], 0), root_doc["root"],
                    team_signature=req.team_signature, nonce_hash=C.nonce_hash(nonce),
                    window_index=0)
    return meta, sidecar


def test_freeze_commits_identity_and_publishes_nonce(fixture_round):
    meta, sidecar = _frozen_entry(fixture_round)
    doc = yaml.safe_load(meta.read_text())
    sc = json.loads(sidecar.read_text())  # rewritten by the hub with its receipt
    assert sc["hub_receipt"]["merkle_position"] == 0
    assert doc["freeze"]["commit"] == sc["commit"]
    assert doc["freeze"]["season_round_id"] == fr_round_id(fixture_round)
    rec = M.load_identity_record(meta)
    assert C.verify_reveal(sc["commit"], M.identity_bytes(rec), b"",
                           bytes.fromhex(doc["freeze"]["nonce"]))


def test_verify_entry_against_a_daily_round(fixture_round):
    meta, _ = _frozen_entry(fixture_round)
    # A daily round that opens after the anchor: scorable.
    later = datetime(2026, 9, 10, 0, 0, tzinfo=UTC)
    rep = F.verify_entry(meta, season_round_dir=fixture_round.round_dir, before=later)
    names = {c.name: c for c in rep.checks}
    assert names["anchor precedes T_open"].ok
    assert names["commitment opens to identity record"].ok
    assert rep.ok
    # A daily round that opened before the anchor: not scorable (FREEZE-05).
    earlier = datetime(2026, 9, 9, 0, 0, tzinfo=UTC)
    rep = F.verify_entry(meta, season_round_dir=fixture_round.round_dir, before=earlier)
    assert not rep.ok
    assert not {c.name: c for c in rep.checks}["anchor precedes T_open"].ok


def test_verify_entry_detects_identity_tampering(fixture_round):
    meta, _ = _frozen_entry(fixture_round)
    doc = yaml.safe_load(meta.read_text())
    rec = dict(doc["model_identity"])
    rec["revision"] = "2" * 40
    rec["identity_hash"] = M.identity_hash(rec)  # internally consistent, but not what was committed
    doc["model_identity"] = rec
    meta.write_text(yaml.safe_dump(doc, sort_keys=False))
    rep = F.verify_entry(meta, season_round_dir=fixture_round.round_dir,
                         before=datetime(2026, 9, 10, tzinfo=UTC))
    assert not {c.name: c for c in rep.checks}["commitment opens to identity record"].ok


def test_verify_entry_checks_snapshot(fixture_round, tmp_path):
    meta, _ = _frozen_entry(fixture_round)
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "config.json").write_bytes(b"0123456789")
    (snap / "m-Q4_K_M.gguf").write_bytes(b"short")  # wrong size → mismatch
    rep = F.verify_entry(meta, season_round_dir=fixture_round.round_dir,
                         before=datetime(2026, 9, 10, tzinfo=UTC), snapshot=snap)
    c = {c.name: c for c in rep.checks}["snapshot matches pinned hashes"]
    assert not c.ok and "size mismatch" in c.detail


def test_verify_entry_snapshot_requires_the_weights_file(fixture_round, tmp_path):
    meta, _ = _frozen_entry(fixture_round)
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "config.json").write_bytes(b"0123456789")
    rep = F.verify_entry(meta, season_round_dir=fixture_round.round_dir,
                         before=datetime(2026, 9, 10, tzinfo=UTC), snapshot=snap)
    c = {c.name: c for c in rep.checks}["snapshot matches pinned hashes"]
    assert not c.ok and "missing m-Q4_K_M.gguf" in c.detail
