"""Frozen model identity for Hugging Face revisions (DESIGN.md §7.1a, rules 2026.10 §2).

An *identity record* pins everything the hub needs to re-run a model and everything a
third party needs to check that what ran is what was committed:

- the full 40-hex commit SHA the revision resolved to, and the HF-reported commit time
  (recorded, never trusted — the freeze time is the external anchor of the Merkle root
  that contains the identity hash);
- every file in that revision with its SHA-256 and size, taken from the Hub's LFS
  metadata (regular, non-LFS files carry a git blob id only; see `_file_entry`);
- the inference configuration (engine, weights file, context, decoding, thinking mode) and the hash of
  the prompt template the hub will use;
- a canonical identity hash over all of the above, which is the committed object.

Network access is confined to the injectable `api` object (a `huggingface_hub.HfApi`
in production, a stub in tests), so everything else here is offline and deterministic.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import commit as C

SCHEMA = "fc-model-identity/2026.10"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# Weight formats the hub will load. Everything else (pickled .bin/.pt/.pkl/.ckpt) is
# arbitrary code at load time and is refused for Tier S (legal-review §2.12).
SAFE_WEIGHT_SUFFIXES = (".safetensors", ".gguf")
UNSAFE_WEIGHT_SUFFIXES = (".bin", ".pt", ".pth", ".pkl", ".pickle", ".ckpt", ".h5")

# Licence ids (HF `license` card field) under which the hub may download, hash, execute
# and publish outputs without manual review (legal-review §2.5).
LICENCE_ALLOWLIST = frozenset({
    "apache-2.0", "mit", "bsd-3-clause", "cc-by-4.0", "cc-by-sa-4.0", "openrail", "openrail++",
    "llama2", "llama3", "llama3.1", "llama3.2", "llama3.3", "llama4", "gemma", "qwen",
    "mistral", "tongyi-qianwen", "deepseek", "other-permissive",
})

DEFAULT_TIER_S_BUDGET_BYTES = 11 * 1024**3  # 12 GB card minus runtime headroom


class HubApi(Protocol):
    """The slice of `huggingface_hub.HfApi` this module uses."""

    def model_info(self, repo_id: str, *, revision: str | None = None,
                   files_metadata: bool = False) -> Any: ...

    def list_repo_commits(self, repo_id: str, *, revision: str | None = None) -> list[Any]: ...


@dataclass
class FileEntry:
    path: str
    size: int | None
    sha256: str | None          # LFS files only
    blob_id: str | None = None  # git blob id for non-LFS files

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"path": self.path, "size": self.size}
        if self.sha256 is not None:
            d["sha256"] = self.sha256
        if self.blob_id is not None:
            d["blob_id"] = self.blob_id
        return d


@dataclass
class Inference:
    engine: str = "llama.cpp"
    weights_file: str | None = None
    context_length: int = 16384
    max_tokens: int = 4096
    temperature: float = 0.0
    seed: str = "from_daily_entropy"
    engine_image: str | None = None  # reference runner image digest, filled from season manifest
    thinking: bool = False           # reasoning mode; frozen, default OFF (rules 2026.10)

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "weights_file": self.weights_file,
            "context_length": self.context_length,
            "decoding": {"temperature": self.temperature, "max_tokens": self.max_tokens,
                         "seed": self.seed},
            "engine_image": self.engine_image,
            "thinking": self.thinking,
        }


@dataclass
class Identity:
    repo_id: str
    revision: str
    hf_commit_time: str | None
    files: list[FileEntry]
    inference: Inference
    prompt_template_sha256: str
    licence: str | None = None
    gated: bool | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def record(self) -> dict[str, Any]:
        """The hashed record: everything above except nothing — `extra` is included too,
        so a participant cannot change e.g. a system prompt without changing the hash."""
        return {
            "schema": SCHEMA,
            "type": "hf",
            "repo_id": self.repo_id,
            "revision": self.revision,
            "hf_commit_time": self.hf_commit_time,
            "licence": self.licence,
            "gated": self.gated,
            "files": [f.to_dict() for f in sorted(self.files, key=lambda f: f.path)],
            "inference": self.inference.to_dict(),
            "prompt_template_sha256": self.prompt_template_sha256,
            "extra": self.extra,
        }

    def hash(self) -> str:
        return identity_hash(self.record())

    def to_dict(self) -> dict[str, Any]:
        rec = self.record()
        rec["identity_hash"] = identity_hash(rec)
        return rec


def identity_hash(record: dict[str, Any]) -> str:
    """SHA-256 over the canonical JSON of a record, with `identity_hash` itself excluded."""
    rec = {k: v for k, v in record.items() if k != "identity_hash"}
    return hashlib.sha256(C.canonical_json(rec)).hexdigest()


def prompt_template_hash(text: str | bytes) -> str:
    data = text.encode() if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- resolve

def _file_entry(sib: Any) -> FileEntry:
    lfs = getattr(sib, "lfs", None)
    if lfs is not None:
        sha = lfs.get("sha256") if isinstance(lfs, dict) else getattr(lfs, "sha256", None)
        size = lfs.get("size") if isinstance(lfs, dict) else getattr(lfs, "size", None)
        return FileEntry(sib.rfilename, size, sha)
    return FileEntry(sib.rfilename, getattr(sib, "size", None), None,
                     blob_id=getattr(sib, "blob_id", None))


def resolve(repo_id: str, revision: str | None, *, api: HubApi,
            inference: Inference | None = None,
            prompt_template: str | bytes = "",
            extra: dict[str, Any] | None = None) -> Identity:
    """Resolve `revision` (ref or sha) of `repo_id` into an Identity.

    Raises ValueError if the Hub does not report a 40-hex commit sha, or if a weight
    file is in an unsafe (pickled) format.
    """
    info = api.model_info(repo_id, revision=revision, files_metadata=True)
    sha = getattr(info, "sha", None)
    if not isinstance(sha, str) or not SHA_RE.match(sha):
        raise ValueError(f"hub returned no commit sha for {repo_id}@{revision!r}")
    files = [_file_entry(s) for s in (getattr(info, "siblings", None) or [])]
    if not files:
        raise ValueError(f"{repo_id}@{sha} lists no files")
    bad = [f.path for f in files if f.path.lower().endswith(UNSAFE_WEIGHT_SUFFIXES)]
    if bad:
        raise ValueError(f"pickled weight files are refused: {', '.join(sorted(bad))}")

    commit_time = None
    try:
        for c in api.list_repo_commits(repo_id, revision=sha):
            if getattr(c, "commit_id", None) == sha:
                ts = getattr(c, "created_at", None)
                commit_time = ts.isoformat().replace("+00:00", "Z") if ts else None
                break
    except Exception:  # noqa: BLE001 — informational field; never block a freeze on it
        commit_time = None

    card = getattr(info, "card_data", None)
    licence = None
    if card is not None:
        licence = card.get("license") if isinstance(card, dict) else getattr(card, "license", None)
    if isinstance(licence, list):
        licence = licence[0] if licence else None

    inf = inference or Inference()
    if inf.weights_file is None:
        inf.weights_file = default_weights_file(files)
    return Identity(
        repo_id=repo_id, revision=sha, hf_commit_time=commit_time, files=files,
        inference=inf, prompt_template_sha256=prompt_template_hash(prompt_template),
        licence=licence, gated=getattr(info, "gated", None), extra=dict(extra or {}),
    )


def default_weights_file(files: list[FileEntry]) -> str | None:
    """Pick the single GGUF if exactly one exists; otherwise None (caller must choose)."""
    ggufs = [f.path for f in files if f.path.lower().endswith(".gguf")]
    if len(ggufs) == 1:
        return ggufs[0]
    return None


# --------------------------------------------------------------------------- gates

@dataclass
class Gate:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def check_entry(identity: Identity, tier: str, *,
                budget_bytes: int = DEFAULT_TIER_S_BUDGET_BYTES) -> Gate:
    """Registration gate (FREEZE-04): hardware budget for Tier S, licence allowlist,
    safe weight format, a chosen weights file."""
    g = Gate()
    weight_files = [f for f in identity.files if f.path.lower().endswith(SAFE_WEIGHT_SUFFIXES)]
    if not weight_files:
        g.errors.append("no safetensors/gguf weight files in revision")
    if tier == "S":
        wf = identity.inference.weights_file
        if wf is None:
            g.errors.append("tier S requires inference.weights_file (one GGUF) to be set")
        else:
            chosen = [f for f in identity.files if f.path == wf]
            if not chosen:
                g.errors.append(f"weights_file {wf!r} not in revision")
            elif chosen[0].size is None:
                g.warnings.append(f"weights_file {wf!r} has no size metadata; budget unchecked")
            elif chosen[0].size > budget_bytes:
                g.errors.append(
                    f"weights_file {wf!r} is {chosen[0].size/1024**3:.1f} GiB; "
                    f"tier S budget is {budget_bytes/1024**3:.1f} GiB — enter as tier A"
                )
            elif chosen[0].sha256 is None:
                g.errors.append(f"weights_file {wf!r} has no LFS sha256; cannot be pinned")
    if identity.licence is None:
        g.errors.append("licence unknown (no `license` in model card); manual review required")
    elif identity.licence not in LICENCE_ALLOWLIST:
        g.errors.append(f"licence {identity.licence!r} not in allowlist; manual review required")
    return g


# --------------------------------------------------------------------------- verify

def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def entry_snapshot_paths(record: dict[str, Any], snapshot: Path) -> list[str]:
    """The pinned files a snapshot must match for a Tier S entry: the chosen weights file
    (always required, so listed first even if absent) plus every other recorded non-weight
    file that exists in the snapshot. Other quants in the repo are not required. The runner
    downloads only the weights file plus ``*.json`` (fc/run.py allow_patterns), and the GGUF
    embeds its tokenizer and chat template, so the weights hash covers what actually runs.
    Without a chosen weights file every recorded path is returned."""
    wf = record["inference"].get("weights_file")
    paths = [f["path"] for f in record["files"]]
    if not wf:
        return paths
    weight_suffixes = SAFE_WEIGHT_SUFFIXES + UNSAFE_WEIGHT_SUFFIXES
    rest = [p for p in paths if p != wf and not p.lower().endswith(weight_suffixes)
            and (snapshot / p).is_file()]
    return [wf, *rest]


def verify_snapshot(record: dict[str, Any], snapshot: Path, *,
                    only: list[str] | None = None) -> list[str]:
    """Compare files in a local snapshot against the record. Returns a list of problems
    (empty = all pinned files match). Files without an LFS sha256 are checked for presence
    and size only."""
    problems: list[str] = []
    for f in record["files"]:
        if only is not None and f["path"] not in only:
            continue
        p = snapshot / f["path"]
        if not p.is_file():
            problems.append(f"missing {f['path']}")
            continue
        if f.get("size") is not None and p.stat().st_size != f["size"]:
            problems.append(f"size mismatch {f['path']}: {p.stat().st_size} != {f['size']}")
            continue
        if f.get("sha256") and sha256_file(p) != f["sha256"]:
            problems.append(f"sha256 mismatch {f['path']}")
    return problems


# --------------------------------------------------------------------------- metadata I/O

def load_identity_record(metadata_path: Path) -> dict[str, Any]:
    """Read `model_identity` from an entry YAML and check its embedded identity hash."""
    import yaml

    doc = yaml.safe_load(metadata_path.read_text())
    rec = doc.get("model_identity")
    if not isinstance(rec, dict) or rec.get("type") != "hf":
        raise ValueError(f"{metadata_path}: no hf model_identity")
    if identity_hash(rec) != rec.get("identity_hash"):
        raise ValueError(f"{metadata_path}: identity_hash does not match record")
    return rec


def identity_bytes(record: dict[str, Any]) -> bytes:
    """The exact bytes `fc commit` hashes for a model entry: canonical JSON of the record
    without the embedded hash (which is derived)."""
    return C.canonical_json({k: v for k, v in record.items() if k != "identity_hash"})
