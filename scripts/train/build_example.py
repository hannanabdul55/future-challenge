"""Tracer A: one historical day -> one SFT example identical to the runner's prompt.

    uv run python scripts/train/build_example.py --cycle 2022 --day 2022-10-31 \
        --family senate --batch-index 0 --out /mnt/d/fc-train/tracer --derive-races

Writes <out>/questions.json, <out>/packet/, <out>/example.jsonl, <out>/prompt.txt and
<out>/summary.json. Exits 1 if the GDELT section is not unavailable, any revision is stamped
after the day's 00:00 UTC, any page failed, the prompt recomputed from the written files
differs from the stored one, or the completion does not round-trip through the live parser.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

from fc import forecast_io as F
from fc import questions as Q
from fc.train import history, packets, paths, pollmodel, sft_data, wiki_asof


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cycle", type=int, required=True, choices=sorted(history.CYCLES))
    ap.add_argument("--day", required=True, help="packet day, YYYY-MM-DD")
    ap.add_argument("--family", required=True, choices=["senate", "governor"])
    ap.add_argument("--batch-index", type=int, default=0)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--cache", type=Path, default=paths.WIKI_CACHE)
    ap.add_argument("--races", type=Path, default=None,
                    help="default data/phase-04.1/races_<cycle>.csv")
    ap.add_argument("--derive-races", action="store_true",
                    help="re-derive the races CSV by title existence at E-8 00:00 UTC")
    args = ap.parse_args()

    cycle = history.CYCLES[args.cycle]
    day = date.fromisoformat(args.day)
    asof = f"{day.isoformat()}T00:00:00Z"
    races = args.races or paths.REPO_DATA / f"races_{cycle.year}.csv"
    cache = wiki_asof.WikiCache(args.cache)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    if args.derive_races:
        rows = history.derive_races(cycle, cache)
        history.write_races(races, rows, cycle)
        n_sen = sum(r["office"] == "senate" for r in rows)
        n_gov = sum(r["office"] == "governor" for r in rows)
        print(f"derived {races}: {n_sen} senate, {n_gov} governor")

    # 1. questions, written then reloaded exactly as the runner loads them
    h = history.write_questions(out / "questions.json", history.build_questions(races, cycle),
                                cycle)
    qs = Q.load_questions(out / "questions.json")

    # 2. packet from point-in-time revisions through the unchanged live renderer
    index = packets.build_day(cycle, day, qs, out / "packet", cache)
    packet_md = (out / "packet" / "packet.md").read_text()

    # 3. the batch the runner would send
    fam_batches = [b for b in F.batches(qs, max_per_batch=20) if b[0]["family"] == args.family]
    if args.batch_index >= len(fam_batches):
        fail(f"family {args.family} has {len(fam_batches)} batches, asked for {args.batch_index}")
    batch = fam_batches[args.batch_index]

    # 4. targets: packet-only probit with the pre-fit defaults (plan 04.1-09 rebuilds with
    #    fitted parameters and the author's prior_mode)
    answers: dict[str, dict[str, float]] = {}
    polled = 0
    for q in batch:
        feat = pollmodel.extract_features(packet_md, q["question_id"])
        polled += not feat.unpolled
        probs = pollmodel.race_probs(feat, pollmodel.DEFAULT_PARAMS, office=q["family"],
                                     prior_mode="packet")
        answers[q["question_id"]] = sft_data.round_probs(probs)

    template = F.load_template()
    ex = sft_data.build_example(cycle=cycle, day=day, packet_md=packet_md, batch=batch,
                                batch_index=args.batch_index, answers=answers, masked_ids=[],
                                template=template)
    try:
        sft_data.check_example(ex, batch, round_id=f"{cycle.season}-tracer")
    except ValueError as exc:
        fail(str(exc))

    # 5. leakage and degradation checks on what was actually built
    if index["sections"].get("gdelt_headlines") != "unavailable":
        fail(f"GDELT section is {index['sections'].get('gdelt_headlines')!r}, not unavailable")
    if index["sections"].get("wikipedia_polls") != "ok":
        fail(f"wikipedia_polls section is {index['sections'].get('wikipedia_polls')!r}")
    attribution = json.loads((out / "packet" / "attribution.json").read_text())
    wiki = next(s for s in attribution["sources"] if s["name"] == "wikipedia_polls")
    pages = wiki["attribution"]["pages"]
    failed = wiki["attribution"].get("failed_pages", [])
    stamps = []
    for key, rec in pages.items():
        rev = cache.rev_at(rec["title"], asof)
        if rev["revid"] != rec["revid"]:
            fail(f"{key}: packet revid {rec['revid']} differs from cache {rev['revid']}")
        stamps.append(rev["timestamp"])
    max_ts = max(stamps)
    if max_ts > asof:
        fail(f"revision stamped {max_ts} is after {asof}")
    if failed:
        fail(f"{len(failed)} page(s) failed: {sorted(failed)}")

    # 6. the prompt recomputed from the files on disk must equal the stored prompt
    again = F.build_prompt(F.load_template(), packet_md=(out / "packet" / "packet.md").read_text(),
                           questions=[b for b in F.batches(Q.load_questions(out / "questions.json"),
                                                           max_per_batch=20)
                                      if b[0]["family"] == args.family][args.batch_index],
                           day=day.isoformat())
    if again != ex["prompt"]:
        fail("prompt recomputed from the written files differs from the stored prompt")

    (out / "example.jsonl").write_text(json.dumps(ex, ensure_ascii=False) + "\n")
    (out / "prompt.txt").write_text(ex["prompt"])
    parsed = F.parse_answer(ex["completion"], batch, round_id="tracer")
    summary = {
        "day": day.isoformat(), "cycle": cycle.year, "family": args.family,
        "batch_index": args.batch_index, "n_questions": len(batch),
        "prompt_chars": len(ex["prompt"]), "completion_chars": len(ex["completion"]),
        "parse_rate": parsed.parse_rate,
        "gdelt_status": index["sections"]["gdelt_headlines"],
        "max_revision_timestamp": max_ts, "pages_total": len(pages),
        "pages_failed": len(failed), "polled_in_batch": polled,
        "packet_md_sha256": hashlib.sha256(packet_md.encode()).hexdigest(),
        "questions_hash": h,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
