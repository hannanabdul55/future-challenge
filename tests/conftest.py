"""Shared fixtures for the Phase 1 commitment-loop tests.

Everything here is offline and deterministic: the fixture round pins `T_commit`
to a fixed instant so deadline behaviour is exercised by passing an explicit
`now` on either side of it, never by reading the wall clock.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from fc import commit as C

ROUND_ID = "2026-w37"
T_COMMIT = "2026-09-09T23:59:00Z"


@dataclass
class FixtureRound:
    """Handle onto a complete round directory built under `tmp_path`."""

    root: Path
    round_dir: Path
    round_id: str
    manifest: dict[str, Any]
    hub_sk: Any
    hub_pk: Any
    hub_key_path: Path
    team_sk: Any
    team_pk: Any

    def request(
        self,
        submission: bytes,
        rationale: bytes = b"",
        model_id: str = "acme-v3",
    ) -> tuple[C.CommitRequest, bytes]:
        """Build a team-signed commit request plus the nonce that opens it."""
        nonce = C.new_nonce()
        c = C.commitment(submission, rationale, nonce)
        req = C.team_sign_request(self.team_sk, self.round_id, model_id, c)
        return req, nonce


def _manifest(round_id: str) -> dict[str, Any]:
    """Mirror the field set of the real rounds/2026-w37/manifest.json."""
    return {
        "round_id": round_id,
        "rules_version": "2026.08",
        "T_open": "2026-09-07T00:00:00Z",
        "T_commit": T_COMMIT,
        "T_entropy": "2026-09-10T00:00:00Z",
        "T_reveal": "2026-09-10T12:00:00Z",
        "T_final_policy": "target_end_date + 28d",
        "tasks_config": "hub-config/tasks.json",
        "tasks_config_sha256": "00" * 32,
        "data_vintage": {
            "path": "target-data/vintages/synthetic-hosp/2026-09-05.csv",
            "sha256": "11" * 32,
        },
        "questions": f"see hub-config/tasks.json rounds[round_id={round_id}]",
    }


@pytest.fixture
def fixture_round(tmp_path: Path) -> FixtureRound:
    """A complete, offline round directory with a hub keypair and one team keypair."""
    round_dir = tmp_path / "rounds" / ROUND_ID
    round_dir.mkdir(parents=True, exist_ok=True)

    manifest = _manifest(ROUND_ID)
    (round_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    hub_priv, hub_pub = C.generate_keypair()
    team_priv, team_pub = C.generate_keypair()

    hub_config = tmp_path / "hub-config"
    hub_config.mkdir(parents=True, exist_ok=True)
    hub_key_path = hub_config / "hub-public.pem"
    hub_key_path.write_bytes(hub_pub)

    return FixtureRound(
        root=tmp_path,
        round_dir=round_dir,
        round_id=ROUND_ID,
        manifest=manifest,
        hub_sk=C.load_private(hub_priv),
        hub_pk=C.load_public(hub_pub),
        hub_key_path=hub_key_path,
        team_sk=C.load_private(team_priv),
        team_pk=C.load_public(team_pub),
    )
