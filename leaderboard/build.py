"""Thin wrapper: `python leaderboard/build.py eval/out leaderboard/index.html`."""
import sys
from pathlib import Path

from fc.leaderboard import build

if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]))
