"""Freeze an entry: resolve its Hugging Face identity, commit the identity hash through the
ordinary commitment service, and verify the freeze against a daily round (FREEZE-01..05).

Nothing here is specific to models once the identity record exists: the committed object is
`model_identity.identity_bytes(record)`, the sidecar is the ordinary sidecar, the receipt is
the ordinary receipt. The nonce is published in the entry YAML immediately — a model
identity is public, so there is nothing to hide between commit and reveal; what the
commitment buys is the anchored *time*, not secrecy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from . import commit as C
from . import model_identity as M
from . import verify_cli as V


@dataclass
class FreezeResult:
    metadata_path: Path
    identity_hash: str
    commit: str | None
    sidecar_path: Path | None


def write_entry_yaml(path: Path, *, model_id: str, team: str, tier: str,
                     identity: M.Identity, public_key_pem: str | None,
                     primary: bool = True, methods: str = "", licence_note: str = "",
                     extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write (or rewrite) `model-metadata/<model_id>.yml` with the identity record.
    Existing non-identity fields are preserved when the file already exists."""
    doc: dict[str, Any] = {}
    if path.exists():
        doc = yaml.safe_load(path.read_text()) or {}
    team_abbr, _, model_abbr = model_id.partition("-")
    doc.update({
        "model_id": model_id,
        "team_abbr": team_abbr,
        "model_abbr": model_abbr or model_id,
        "team_name": doc.get("team_name", team),
        "primary": doc.get("primary", primary),
        "tier": tier,
        "methods": doc.get("methods", methods),
        "licence_note": doc.get("licence_note", licence_note),
        "model_identity": identity.to_dict(),
        "public_key_pem": public_key_pem if public_key_pem is not None
        else doc.get("public_key_pem"),
    })
    if extra:
        doc.update(extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))
    return doc


def commit_identity(metadata_path: Path, *, season_round_id: str, team_sk,
                    sidecar_path: Path) -> tuple[C.CommitRequest, bytes]:
    """Build the team-signed commit request for the entry's identity record and write
    the pre-receipt sidecar (exactly what `fc commit` writes for a forecast file)."""
    rec = M.load_identity_record(metadata_path)
    doc = yaml.safe_load(metadata_path.read_text())
    nonce = C.new_nonce()
    c = C.commitment(M.identity_bytes(rec), b"", nonce)
    req = C.team_sign_request(team_sk, season_round_id, doc["model_id"], c)
    sidecar = {"round_id": season_round_id, "model_id": doc["model_id"], "commit": c,
               "team_signature": req.team_signature,
               "created": datetime.now(UTC).isoformat(),
               "committed_object": "model_identity",
               "identity_hash": rec["identity_hash"]}
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(json.dumps(sidecar, indent=2) + "\n")
    # Publish the nonce beside the identity: the record is public, the time is the secret.
    doc["freeze"] = {"season_round_id": season_round_id, "sidecar": str(sidecar_path),
                     "commit": c, "nonce": nonce.hex()}
    metadata_path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))
    return req, nonce


def verify_entry(metadata_path: Path, *, season_round_dir: Path, before: datetime | None,
                 before_label: str = "T_open", snapshot: Path | None = None,
                 hub_key_path: Path | None = None, headers_path: Path | None = None,
                 allow_pending: bool = False, ots_parser=None) -> V.VerifyReport:
    """FREEZE-03: the entry's identity hash is in an anchored root whose anchor precedes
    `before` (a daily round's `T_open`), the commitment opens to the identity record, and
    (optionally) a local snapshot matches the pinned file hashes."""
    doc = yaml.safe_load(metadata_path.read_text())
    freeze = doc.get("freeze") or {}
    sidecar = Path(freeze.get("sidecar", ""))
    if not sidecar.is_absolute():
        sidecar = metadata_path.parent.parent / sidecar
    rep = V.verify(sidecar, roots_dir=season_round_dir / "roots",
                   manifest_path=season_round_dir / "manifest.json",
                   hub_key_path=hub_key_path, headers_path=headers_path,
                   allow_pending=allow_pending, ots_parser=ots_parser,
                   before=before, before_label=before_label)

    try:
        rec = M.load_identity_record(metadata_path)
        opens = C.verify_reveal(freeze["commit"], M.identity_bytes(rec), b"",
                                bytes.fromhex(freeze["nonce"]))
        rep.add("commitment opens to identity record", opens,
                f"identity {rec['identity_hash'][:16]}… for {rec['repo_id']}@{rec['revision'][:12]}",
                V.TRUSTLESS)
    except (KeyError, ValueError, TypeError) as exc:
        rep.add("commitment opens to identity record", False, str(exc), V.TRUSTLESS)
        rec = None

    if snapshot is not None and rec is not None:
        problems = M.verify_snapshot(rec, Path(snapshot))
        rep.add("snapshot matches pinned hashes", not problems,
                "; ".join(problems) if problems else f"{len(rec['files'])} file(s) checked",
                V.TRUSTLESS)
    return rep
