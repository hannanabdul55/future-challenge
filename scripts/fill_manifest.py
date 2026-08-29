"""Fill sha256 fields of a round manifest (tasks config, data vintage) and print the manifest hash to anchor."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def sha(p: str) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main(path: str):
    m = json.loads(Path(path).read_text())
    m["tasks_config_sha256"] = sha(m["tasks_config"])
    if Path(m["data_vintage"]["path"]).exists():
        m["data_vintage"]["sha256"] = sha(m["data_vintage"]["path"])
    Path(path).write_text(json.dumps(m, indent=2) + "\n")
    print(f"manifest sha256 {sha(path)}  <- anchor via OpenTimestamps + Rekor before T_commit")


if __name__ == "__main__":
    main(sys.argv[1])
