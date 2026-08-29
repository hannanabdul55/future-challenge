"""Behavioral fingerprint of API models (DESIGN.md §7.3).

A fixed probe panel is run at temperature 0 at T_commit and again at T_run. Drift above
tolerance flags the entry as "model changed". The panel lives in fingerprint/probes.json.
`call` is any function prompt -> str; provider adapters are out of scope for the scaffold.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PANEL = Path(__file__).resolve().parent.parent / "fingerprint" / "probes.json"


@dataclass
class Fingerprint:
    panel_hash: str
    outputs: dict[str, str]  # probe_id -> output

    def to_json(self) -> str:
        return json.dumps({"panel_hash": self.panel_hash, "outputs": self.outputs}, indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> Fingerprint:
        d = json.loads(s)
        return cls(d["panel_hash"], d["outputs"])


def load_panel(path: str | Path = DEFAULT_PANEL) -> list[dict]:
    return json.loads(Path(path).read_text())["probes"]


def panel_hash(probes: list[dict]) -> str:
    return hashlib.sha256(json.dumps(probes, sort_keys=True).encode()).hexdigest()


def capture(call: Callable[[str], str], probes: list[dict]) -> Fingerprint:
    return Fingerprint(panel_hash(probes), {p["id"]: call(p["prompt"]) for p in probes})


def _norm_edit(a: str, b: str) -> float:
    """Normalized Levenshtein distance in [0,1]."""
    if a == b:
        return 0.0
    if not a or not b:
        return 1.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] / max(len(a), len(b))


def drift(before: Fingerprint, after: Fingerprint) -> dict:
    if before.panel_hash != after.panel_hash:
        raise ValueError("fingerprints from different panels")
    ids = sorted(before.outputs)
    exact = sum(before.outputs[i] != after.outputs.get(i, "") for i in ids)
    edits = [_norm_edit(before.outputs[i], after.outputs.get(i, "")) for i in ids]
    return {
        "n_probes": len(ids),
        "changed_fraction": exact / len(ids) if ids else 0.0,
        "mean_edit_distance": sum(edits) / len(edits) if edits else 0.0,
    }


def changed(before: Fingerprint, after: Fingerprint, tol_fraction: float = 0.05) -> bool:
    return drift(before, after)["changed_fraction"] > tol_fraction
