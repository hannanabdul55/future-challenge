"""Measure live packets and derive the historical packet budget (D-04, plan 04.1-06).

    uv run python scripts/train/measure_live_packet.py /mnt/d/fc-train/livepacket/<date> [...]

Reads each live packet directory (packet.md and index.json built by ``uv run fc packet build``),
records the section lengths and statuses, and writes data/phase-04.1/live_packet_stats.json with
``hist_budget_chars`` derived from the median headlines length of the builds whose GDELT
section came back ok. Nothing here touches the network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path

from fc import packet as P
from fc.train import packets

FORMULA = (
    "fc.packet.build gives polls (weight 3 of 5) its share plus every character the other "
    "sections left unused, so polls allowance = budget - len(markets section) - "
    "len(headlines section). Live: 20000 - len_markets - len_headlines (median headlines "
    "length over the builds with gdelt_status ok). Historical: hist_budget_chars - "
    "len(markets render, empty mapping) - len(GdeltUnavailable render). hist_budget_chars is "
    "the value that makes the two equal (fc.train.packets.derive_hist_budget). A live day "
    "whose GDELT call fails gives polls the same allowance as a historical packet, because "
    "both then carry the degraded headlines render."
)


def measure(packet_dir: Path) -> dict:
    md = (packet_dir / "packet.md").read_bytes()
    index = json.loads((packet_dir / "index.json").read_text())
    secs = packets.split_sections(md.decode())

    def section(prefix: str) -> str:
        return next(t for h, t in secs.items() if h.startswith(prefix))

    return {
        "date": index["day"],
        "packet_md_sha256": hashlib.sha256(md).hexdigest(),
        "len_polls": len(section("## Polls")),
        "len_markets": len(section("## Prediction")),
        "len_headlines": len(section("## Headlines")),
        "gdelt_status": index["sections"]["gdelt_headlines"],
        "markets_status": index["sections"]["prediction_markets"],
        "polls_status": index["sections"]["wikipedia_polls"],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dirs", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=packets.LIVE_STATS)
    a = ap.parse_args(argv)
    builds = [measure(d) for d in a.dirs]
    ok = [b for b in builds if b["gdelt_status"] == "ok"]
    if not ok:
        print("FAIL: no live build had GDELT ok; wait 10 minutes and rebuild once", file=sys.stderr)
        return 1
    headlines = int(statistics.median(b["len_headlines"] for b in ok))
    markets = int(statistics.median(b["len_markets"] for b in ok))
    budget = packets.derive_hist_budget(P.DEFAULT_BUDGET_CHARS, markets, headlines)
    live_allowance = P.DEFAULT_BUDGET_CHARS - markets - headlines
    doc = {
        "builds": builds,
        "live_budget_chars": P.DEFAULT_BUDGET_CHARS,
        "median_len_headlines": headlines,
        "median_len_markets": markets,
        "live_polls_allowance": live_allowance,
        "hist_budget_chars": budget,
        "formula": FORMULA,
        "assumption_A10": (
            f"settled by measurement: the live GDELT headlines section was {headlines} of its "
            f"{P.DEFAULT_BUDGET_CHARS // 5} character share"),
        "note": "A live day whose GDELT call fails gives polls about the same allowance as the "
                "historical packets (both carry the degraded headlines render); a live day "
                "whose GDELT call works gives the measured live allowance above.",
    }
    a.out.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"hist_budget_chars={budget} live_polls_allowance={live_allowance}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
