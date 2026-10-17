"""Check the hashed lock files under scripts/train/locks before any venv is installed.

Findings (one per line, exit 1 if any):
  UNHASHED <lock> <name>      a requirement block carries no --hash=sha256: entry
  MISSING PIN <lock> <pin>    a required top-level pin is absent from the lock
  BAD SOURCE <detail>         sources.json lacks the converter commit, a 64-hex tarball
                              sha256, or a lock-file sha256 that matches the file on disk
Otherwise prints "locks ok: N requirements in 4 locks".

Stdlib only. Usage: uv run python scripts/train/check_locks.py scripts/train/locks
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

LOCKS = ("train.lock", "unsloth.lock", "convert.lock", "eval.lock")
CONVERTER_COMMIT = "4df29be4f4c3673f428170fda944a5b19f743bb8"

# lock file -> top-level pins that must be present. A pin ending in "==" only
# requires the package to be pinned to some exact version.
REQUIRED_PINS: dict[str, tuple[str, ...]] = {
    "train.lock": (
        "torch==2.14.1",
        "transformers==5.20.1",
        "trl==1.16.0",
        "peft==0.21.2",
        "bitsandbytes==0.50.2",
        "accelerate==1.15.0",
        "liger-kernel==0.8.4",
    ),
    "unsloth.lock": ("unsloth==2026.10.6",),
    "convert.lock": ("transformers==", "torch=="),
    "eval.lock": ("pandas==", "numpy==", "jinja2==", "diskcache=="),
}

HEX64 = re.compile(r"^[0-9a-f]{64}$")
BLOCK_START = re.compile(r"^[A-Za-z0-9]")


def parse_blocks(text: str) -> list[tuple[str, str]]:
    """Return (requirement spec, full block text) for each requirement block.

    A block starts at a line beginning with a package name and continues over the
    indented hash and comment lines that follow it.
    """
    blocks: list[list[str]] = []
    for line in text.splitlines():
        if BLOCK_START.match(line):
            blocks.append([line])
        elif blocks and line[:1] in (" ", "\t"):
            blocks[-1].append(line)
    out = []
    for lines in blocks:
        spec = lines[0].rstrip("\\").split(";")[0].strip().lower()
        out.append((spec, "\n".join(lines)))
    return out


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_sources(locks_dir: Path) -> list[str]:
    path = locks_dir / "sources.json"
    if not path.exists():
        return ["BAD SOURCE sources.json is missing"]
    try:
        src = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return [f"BAD SOURCE sources.json does not parse: {exc}"]
    findings = []
    tar = src.get("llama_cpp_tarball", {})
    if tar.get("commit") != CONVERTER_COMMIT:
        findings.append(f"BAD SOURCE llama_cpp_tarball.commit is not {CONVERTER_COMMIT}")
    if not HEX64.match(str(tar.get("sha256", ""))):
        findings.append("BAD SOURCE llama_cpp_tarball.sha256 is not 64 hex characters")
    recorded = src.get("lock_sha256", {})
    for name in LOCKS:
        lock = locks_dir / name
        if lock.exists() and recorded.get(name) != sha256_file(lock):
            findings.append(f"BAD SOURCE lock_sha256 for {name} does not match the file on disk")
    return findings


def main() -> int:
    locks_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "scripts/train/locks")
    findings: list[str] = []
    total = 0
    for name in LOCKS:
        path = locks_dir / name
        if not path.exists():
            findings.append(f"MISSING PIN {name} (lock file not found)")
            continue
        blocks = parse_blocks(path.read_text())
        total += len(blocks)
        for spec, block in blocks:
            if "--hash=sha256:" not in block:
                findings.append(f"UNHASHED {name} {spec.split('==')[0]}")
        specs = [spec for spec, _ in blocks]
        for pin in REQUIRED_PINS[name]:
            if not any(s.startswith(pin) for s in specs):
                findings.append(f"MISSING PIN {name} {pin}")
    findings.extend(check_sources(locks_dir))
    if findings or total == 0:
        for line in findings:
            print(line)
        return 1
    print(f"locks ok: {total} requirements in {len(LOCKS)} locks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
