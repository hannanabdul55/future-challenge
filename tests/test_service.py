"""Service-core behaviour: receipts, deadline enforcement, and the reveal close.

`test_issue_receipt` is live from plan 01-01. The remaining three are Wave 0
scaffolding for later plans and are marked strict xfail, so the suite fails the
moment a plan implements the behaviour without removing its marker.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives import serialization

from fc import commit as C

BEFORE_COMMIT = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
AFTER_COMMIT = datetime(2026, 9, 10, 0, 30, 0, tzinfo=UTC)
BEFORE_REVEAL = datetime(2026, 9, 10, 8, 0, 0, tzinfo=UTC)
AFTER_REVEAL = datetime(2026, 9, 10, 12, 30, 0, tzinfo=UTC)


def test_issue_receipt(fixture_round):
    """COMMIT-01: a valid request earns a hub-signed receipt; a forged one earns nothing."""
    from fc import service as S

    fr = fixture_round
    req, _nonce = fr.request(b"sub", b"rat")

    receipt, outcome = S.submit_commitment(
        req, fr.team_pk, fr.hub_sk, fr.round_dir, now=BEFORE_COMMIT
    )
    assert outcome == "accepted"
    assert receipt is not None
    assert receipt.merkle_position == 0
    assert receipt.server_time == C.iso_z(BEFORE_COMMIT)
    assert C.verify_receipt(fr.hub_pk, receipt)

    # A request checked against the wrong team key is refused and leaves no trace.
    wrong_pk = C.load_public(C.generate_keypair()[1])
    before = len(S.read_ledger(fr.round_dir))
    forged, outcome = S.submit_commitment(
        req, wrong_pk, fr.hub_sk, fr.round_dir, now=BEFORE_COMMIT
    )
    assert forged is None
    assert outcome == "bad_signature"
    accepted = [e for e in S.read_ledger(fr.round_dir) if e["outcome"] == "accepted"]
    assert len(accepted) == before


def _register(fr, model_id: str, pub_pem: bytes) -> None:
    """Write the entry metadata that is the only source of a team's key."""
    meta = fr.root / "model-metadata"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / f"{model_id}.yml").write_text(
        f"model_id: {model_id}\ntier: B\nprimary: true\n"
        f"public_key_pem: |\n" + "".join(f"  {ln}\n" for ln in pub_pem.decode().splitlines())
    )
    return meta


def _payload(req, nonce=None):
    d = {
        "round_id": req.round_id,
        "model_id": req.model_id,
        "commit": req.commit,
        "team_signature": req.team_signature,
    }
    if nonce is not None:
        d["nonce_hash"] = C.nonce_hash(nonce)
    return d


def test_pr_and_api_paths_equal(fixture_round):
    """COMMIT-06: the PR path and the API path are one call surface, byte for byte."""
    from fc import service as S

    fr = fixture_round
    team_pub = fr.team_pk.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )

    # Two independent rounds so each front end starts from an identical state.
    receipts = []
    for i, front_end in enumerate(("pr", "api")):
        round_dir = fr.root / f"rounds-{front_end}" / fr.round_id
        round_dir.mkdir(parents=True, exist_ok=True)
        (round_dir / "manifest.json").write_text(
            json.dumps(fr.manifest, indent=2) + "\n"
        )
        meta = _register(fr, "acme-v3", team_pub)
        req, nonce = fr.request(b"sub", b"rat")

        if front_end == "pr":
            sidecar = fr.root / "incoming" / "acme-v3.json"
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_text(json.dumps(_payload(req, nonce), indent=2) + "\n")
            receipt, outcome = S.submit_from_sidecar_file(
                sidecar, fr.hub_sk, round_dir, metadata_dir=meta, now=BEFORE_COMMIT
            )
        else:
            receipt, outcome = S.submit_from_mapping(
                _payload(req, nonce), fr.hub_sk, round_dir,
                metadata_dir=meta, now=BEFORE_COMMIT,
            )
        assert outcome == "accepted"
        assert receipt is not None
        receipts.append((receipt, req))

    (pr_receipt, pr_req), (api_receipt, api_req) = receipts
    # Identical commitments must yield identical signed bytes. Comparing the
    # serialized payload rather than field-by-field means a field added to
    # Receipt later is covered automatically.
    assert pr_req.commit != api_req.commit  # different nonces, as a real pair would be
    rebuilt = C.Receipt(
        pr_receipt.round_id, pr_receipt.model_id, api_receipt.commit,
        pr_receipt.server_time, pr_receipt.merkle_position, "",
    )
    assert rebuilt.payload() == api_receipt.payload()
    assert pr_receipt.server_time == api_receipt.server_time
    assert pr_receipt.merkle_position == api_receipt.merkle_position


def test_team_key_comes_from_metadata_not_payload(fixture_round):
    """A request cannot nominate the key that validates it."""
    from fc import service as S

    fr = fixture_round
    # Register a DIFFERENT key than the one that actually signed the request.
    other_priv, other_pub = C.generate_keypair()
    meta = _register(fr, "acme-v3", other_pub)

    req, nonce = fr.request(b"sub", b"rat")
    payload = _payload(req, nonce)
    # The attacker helpfully supplies the key that would validate their forgery.
    payload["public_key_pem"] = fr.team_pk.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()

    receipt, outcome = S.submit_from_mapping(
        payload, fr.hub_sk, fr.round_dir, metadata_dir=meta, now=BEFORE_COMMIT
    )
    assert receipt is None
    assert outcome == "bad_signature"

    # And the registered key does validate its own signer.
    C.load_private(other_priv)


def test_oversized_sidecar_rejected(fixture_round):
    """The size cap fires before any cryptographic work."""
    from fc import service as S

    fr = fixture_round
    meta = _register(fr, "acme-v3", b"unused")
    fat = fr.root / "fat.json"
    fat.write_text("x" * (S.MAX_SIDECAR_BYTES + 1))

    try:
        S.submit_from_sidecar_file(
            fat, fr.hub_sk, fr.round_dir, metadata_dir=meta, now=BEFORE_COMMIT
        )
        raise AssertionError("oversized sidecar must be refused")
    except ValueError as exc:
        assert "exceeds" in str(exc)


def test_malformed_payload_rejected_before_crypto(fixture_round):
    """Required keys, hex shape and identifier charset are gates, not suggestions."""
    from fc import service as S

    fr = fixture_round
    meta = _register(fr, "acme-v3", b"unused")
    req, _nonce = fr.request(b"sub", b"rat")

    bad_payloads = [
        ({}, "missing required field"),
        ({**_payload(req), "commit": "NOTHEX" * 10}, "invalid commit"),
        ({**_payload(req), "commit": req.commit.upper()}, "invalid commit"),
        ({**_payload(req), "model_id": "../escape"}, "invalid model_id"),
        ({**_payload(req), "team_signature": "zz"}, "invalid team_signature"),
    ]
    for payload, expected in bad_payloads:
        try:
            S.submit_from_mapping(
                payload, fr.hub_sk, fr.round_dir, metadata_dir=meta, now=BEFORE_COMMIT
            )
            raise AssertionError(f"should have refused: {payload}")
        except ValueError as exc:
            assert expected in str(exc), f"{expected!r} not in {exc!r}"


def test_reject_after_deadline(fixture_round):
    """ROUND-02: a late commitment is refused, and the refusal is public."""
    from fc import service as S

    fr = fixture_round
    req, _nonce = fr.request(b"late", b"")

    receipt, outcome = S.submit_commitment(
        req, fr.team_pk, fr.hub_sk, fr.round_dir, now=AFTER_COMMIT
    )
    assert receipt is None
    assert outcome == "after_T_commit"

    ledger = S.read_ledger(fr.round_dir)
    late = [e for e in ledger if e["commit"] == req.commit]
    assert len(late) == 1
    assert late[0]["outcome"] == "rejected"
    assert late[0]["reason"] == "after_T_commit"
    assert late[0]["received"] == C.iso_z(AFTER_COMMIT)


def test_commit_window_closes_at_the_instant(fixture_round):
    """The window closes *at* T_commit — the deadline instant is already late."""
    from fc import service as S

    fr = fixture_round
    at_deadline = datetime.fromisoformat(fr.manifest["T_commit"])
    req, _nonce = fr.request(b"exactly-on-time", b"")

    receipt, outcome = S.submit_commitment(
        req, fr.team_pk, fr.hub_sk, fr.round_dir, now=at_deadline
    )
    assert receipt is None
    assert outcome == "after_T_commit"


def test_rejected_commit_visible_in_ledger(fixture_round):
    """A refused commitment is published, and advances no root."""
    from fc import service as S

    fr = fixture_round
    roots_dir = fr.round_dir / "roots"

    before = len(list(roots_dir.glob("*.json"))) if roots_dir.exists() else 0
    req, _nonce = fr.request(b"late", b"")
    S.submit_commitment(req, fr.team_pk, fr.hub_sk, fr.round_dir, now=AFTER_COMMIT)
    after = len(list(roots_dir.glob("*.json"))) if roots_dir.exists() else 0

    entry = S.read_ledger(fr.round_dir)[-1]
    assert entry["outcome"] == "rejected"
    assert entry["reason"] == "after_T_commit"
    assert entry["received"] == C.iso_z(AFTER_COMMIT)
    assert entry["merkle_position"] is None
    assert entry["window_index"] is None
    # The request is self-contained evidence of what was attempted.
    assert entry["commit"] == req.commit
    assert entry["team_signature"] == req.team_signature
    # A rejected request never advances the root chain.
    assert after == before


def test_late_bad_signature_recorded_as_bad_signature(fixture_round):
    """A forged late request is recorded as forged, not as a legitimate late attempt."""
    from fc import service as S

    fr = fixture_round
    req, _nonce = fr.request(b"forged-and-late", b"")
    wrong_pk = C.load_public(C.generate_keypair()[1])

    receipt, outcome = S.submit_commitment(
        req, wrong_pk, fr.hub_sk, fr.round_dir, now=AFTER_COMMIT
    )
    assert receipt is None
    assert outcome == "bad_signature"

    entry = S.read_ledger(fr.round_dir)[-1]
    assert entry["outcome"] == "rejected"
    assert entry["reason"] == "bad_signature"


@pytest.mark.xfail(strict=True, reason="Wave 0 scaffold — implemented in plan 01-05")
def test_reveal_close_report(fixture_round):
    """COMMIT-05 / ROUND-03: every commitment lands in exactly one of three lists."""
    from fc import service as S

    fr = fixture_round

    matched_req, matched_nonce = fr.request(b"honest", b"why", model_id="matched-v1")
    bad_req, bad_nonce = fr.request(b"switcheroo", b"why", model_id="invalid-v1")
    silent_req, _silent_nonce = fr.request(b"vanishes", b"", model_id="missing-v1")

    for req in (matched_req, bad_req, silent_req):
        _r, outcome = S.submit_commitment(
            req, fr.team_pk, fr.hub_sk, fr.round_dir, now=BEFORE_COMMIT
        )
        assert outcome == "accepted"

    S.record_reveal(
        fr.round_dir, "matched-v1", b"honest", b"why", matched_nonce, now=BEFORE_REVEAL
    )
    # Reveals different bytes than were committed to.
    S.record_reveal(
        fr.round_dir, "invalid-v1", b"different", b"why", bad_nonce, now=BEFORE_REVEAL
    )

    report = S.close_reveals(fr.round_dir, now=AFTER_REVEAL)
    assert report["matched"] == ["matched-v1"]
    assert report["invalid"] == ["invalid-v1"]
    assert report["missing"] == ["missing-v1"]
    # Nothing may be quietly dropped: the three lists partition the accepted set.
    accepted = [e for e in S.read_ledger(fr.round_dir) if e["outcome"] == "accepted"]
    assert len(report["matched"]) + len(report["invalid"]) + len(report["missing"]) == len(
        accepted
    )
