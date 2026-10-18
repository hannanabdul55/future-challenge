"""Historical packets through the unchanged live renderer (D-03, D-04).

``fc.packet.build`` is called with an injected ``Http`` that serves point-in-time Wikipedia
revisions, the same three sources in the same order and weights as ``P.default_sources``
(polls 3, markets 1, headlines 1), and a GDELT stand-in that is explicitly unavailable
because the GDELT DOC API reaches back only about three months.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from fc import packet as P

from . import history, paths, wiki_asof

LIVE_STATS = paths.REPO_DATA / "live_packet_stats.json"

HIST_GDELT_REASON = "historical packet: the GDELT DOC API reaches back only about 3 months"


@dataclass
class GdeltUnavailable:
    """Stand-in for ``P.GdeltHeadlines``: always unavailable, never touches the network."""
    name: str = "gdelt_headlines"
    licence: str = "GDELT terms (free, attribution)"
    budget_weight: int = 1
    reason: str = HIST_GDELT_REASON

    def fetch(self, http: P.Http, day: date) -> P.Section:
        return P.Section(self.name, "unavailable", self.licence, {}, reason=self.reason)

    def render(self, section: P.Section, budget: int) -> str:
        # exactly the degraded form P.GdeltHeadlines.render emits
        return f"## Headlines\n\n_unavailable: {section.reason}_\n"


def historical_budget(stats_path: Path = LIVE_STATS) -> int:
    """Packet budget (characters) that gives a historical packet the same polls allowance as a
    measured live packet (D-04). Written by plan 04.1-06 from a live build; a missing file is
    an error, never a silent fallback to 20000."""
    return int(json.loads(Path(stats_path).read_text())["hist_budget_chars"])


def split_sections(packet_md: str) -> dict[str, str]:
    """packet.md cut at its "## " section headers: header line -> section text (with the
    trailing newline the renderer adds). The title block before the first "## " is dropped."""
    parts = re.split(r"(?m)^(?=## )", packet_md)
    return {p.split("\n", 1)[0]: p for p in parts if p.startswith("## ")}


def polls_section(packet_md: str) -> str:
    """The "## Polls" section text of a packet (live or historical)."""
    for head, text in split_sections(packet_md).items():
        if head.startswith("## Polls"):
            return text
    raise ValueError("packet has no polls section")


def derive_hist_budget(live_budget: int, len_markets: int, len_headlines: int) -> int:
    """Historical ``budget_chars`` giving polls the live allowance.

    ``fc.packet.build`` hands every character the other sections did not use to polls, so
    the polls allowance is budget minus the markets and headlines sections. Live:
    ``live_budget - len_markets - len_headlines``. Historical: ``budget - len(markets render of
    an empty mapping) - len(GdeltUnavailable render)``. Equal allowances give
    ``budget = live_allowance + those two lengths``."""
    empty_markets = P.PredictionMarkets({})
    markets_hist = len(empty_markets.render(empty_markets.fetch(_no_http, date(2000, 1, 1)), 0))
    gdelt = GdeltUnavailable()
    gdelt_hist = len(gdelt.render(gdelt.fetch(_no_http, date(2000, 1, 1)), 0))
    return live_budget - len_markets - len_headlines + markets_hist + gdelt_hist


def _no_http(url: str, params: dict[str, Any] | None = None) -> str:
    raise RuntimeError(f"unexpected network call {url!r}")


def historical_sources(questions: list[dict[str, Any]], cycle: history.Cycle) -> list[P.Source]:
    pages = dict(history.national_pages(cycle.year))
    for q in questions:
        title = history.race_title(q, cycle.year)
        if title:
            pages[q["question_id"]] = title
    return [P.WikipediaPolls(pages=pages, pause_s=0), P.PredictionMarkets({}),
            GdeltUnavailable()]


def build_day(cycle: history.Cycle, day: date, questions: list[dict[str, Any]], out_dir: Path,
              cache: wiki_asof.WikiCache, *,
              budget_chars: int | None = None) -> dict[str, Any]:
    """Build the packet for ``day`` from revisions current at ``day`` 00:00 UTC. The default
    budget is the live-matched ``historical_budget()`` (D-04)."""
    return P.build(
        day, Path(out_dir), sources=historical_sources(questions, cycle),
        http=wiki_asof.historical_http(cache, f"{day.isoformat()}T00:00:00Z"),
        season=cycle.season,
        budget_chars=historical_budget() if budget_chars is None else budget_chars,
        now=datetime(day.year, day.month, day.day, tzinfo=UTC))
