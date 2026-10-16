"""The daily information packet (PACKET-01..03; DESIGN.md §3.4).

Every model reads the same packet. It is assembled from sources snapshotted on day D−1,
rendered deterministically to ``packet.json`` and ``packet.md``, archived with the raw
responses and an attribution file, and hashed into ``index.json`` — the hash the daily
round manifest pins.

Each source degrades independently: a failure becomes an ``unavailable`` section with the
reason, never an exception out of ``build``. Network access goes through the injectable
``http`` callable so tests run offline.

Sources (legal-review §2.4):
- Wikipedia polling pages via the MediaWiki API — CC BY-SA 4.0, attribution by revision id
- Prediction-market prices for mapped questions (Polymarket Gamma, Kalshi public API)
- GDELT article list — titles and URLs only
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from . import commit as C

SCHEMA = "fc-packet/2026.10"
PACKET_LICENCE = "CC-BY-SA-4.0"
USER_AGENT = "future-challenge-packet/0.1 (+https://github.com/future-challenge)"
DEFAULT_BUDGET_CHARS = 60_000

Http = Callable[[str, dict[str, Any] | None], str]


def urllib_http(url: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0) -> str:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- source protocol

@dataclass
class Section:
    name: str
    status: str                      # "ok" | "unavailable"
    licence: str
    attribution: dict[str, Any]
    data: Any = None
    reason: str | None = None
    raw: str | None = None           # archived verbatim, never rendered into the packet


class Source(Protocol):
    name: str
    licence: str

    def fetch(self, http: Http, day: date) -> Section: ...

    def render(self, section: Section, budget: int) -> str: ...


# --------------------------------------------------------------------------- Wikipedia polls

WIKI_API = "https://en.wikipedia.org/w/api.php"
# National pages carry the generic-ballot polling; per-race pages carry race polling.
WIKI_PAGES = {
    "house": "2026 United States House of Representatives elections",
    "senate": "2026 United States Senate elections",
}


def race_page_title(q: dict[str, Any]) -> str | None:
    """Wikipedia article for a race question (verified against live titles 2026-10-16)."""
    fam, name = q.get("family"), q.get("state_name")
    if fam == "senate":
        special = " special" if "special" in q.get("statement", "") else ""
        return f"2026 United States Senate{special} election in {name}"
    if fam == "governor":
        return f"2026 {name} gubernatorial election"
    return None

_LINK = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]")
_REF = re.compile(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", re.DOTALL)
_TMPL = re.compile(r"\{\{[^{}]*\}\}")
_TAG = re.compile(r"<[^>]+>")


def strip_wiki(s: str) -> str:
    s = _REF.sub("", s)
    for _ in range(3):
        s = _TMPL.sub(lambda m: _tmpl_value(m.group(0)), s)
    s = _LINK.sub(r"\1", s)
    s = _TAG.sub("", s)
    s = s.replace("'''", "").replace("''", "").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", s).strip()


def _tmpl_value(t: str) -> str:
    """Keep the useful payload of common inline templates ({{party shading}}, {{nowrap|x}})."""
    inner = t[2:-2]
    parts = inner.split("|")
    if len(parts) >= 2 and parts[0].strip().lower() in ("nowrap", "small", "abbr", "sort"):
        return parts[1]
    return ""


def parse_wikitables(wikitext: str) -> list[dict[str, Any]]:
    """Extract every wikitable as {heading, rows:[[cell,...],...]}. Heuristic, not a parser:
    the packet keeps the raw wikitext beside it, so a mis-parse is recoverable."""
    tables: list[dict[str, Any]] = []
    heading = ""
    cur: list[list[str]] | None = None
    row: list[str] = []
    for line in wikitext.splitlines():
        s = line.strip()
        if s.startswith("=") and s.endswith("="):
            heading = s.strip("= ").strip()
            continue
        if s.startswith("{|"):
            cur, row = [], []
            continue
        if cur is None:
            continue
        if s.startswith("|}"):
            if row:
                cur.append(row)
            if cur:
                tables.append({"heading": heading, "rows": cur})
            cur, row = None, []
            continue
        if s.startswith("|-"):
            if row:
                cur.append(row)
            row = []
            continue
        if s.startswith(("!", "|")):
            body = s[1:]
            cells = re.split(r"\|\||!!", body)
            for c in cells:
                c = c.split("|", 1)[1] if "|" in c and "[[" not in c.split("|", 1)[0] else c
                row.append(strip_wiki(c))
    return tables


def _is_poll_table(t: dict[str, Any]) -> bool:
    head = t["heading"].lower()
    first = " ".join(t["rows"][0]).lower() if t["rows"] else ""
    return "poll" in head or "poll" in first


@dataclass
class WikipediaPolls:
    pages: dict[str, str] = field(default_factory=lambda: dict(WIKI_PAGES))
    max_rows_per_table: int = 8
    max_tables_per_page: int = 4
    pause_s: float = 0.5
    name: str = "wikipedia_polls"
    licence: str = "CC-BY-SA-4.0"

    @classmethod
    def from_questions(cls, questions: list[dict[str, Any]], **kw) -> WikipediaPolls:
        pages = dict(WIKI_PAGES)
        for q in questions:
            title = race_page_title(q)
            if title:
                pages[q["question_id"]] = title
        return cls(pages=pages, **kw)

    def fetch(self, http: Http, day: date) -> Section:
        out: dict[str, Any] = {}
        attribution: dict[str, Any] = {"pages": {}}
        raws: dict[str, str] = {}
        errors: list[str] = []
        for i, (key, title) in enumerate(self.pages.items()):
            if i and self.pause_s:
                time.sleep(self.pause_s)
            try:
                txt = http(WIKI_API, {"action": "parse", "page": title, "prop": "wikitext|revid",
                                      "format": "json", "formatversion": "2"})
                doc = json.loads(txt)["parse"]
                raws[key] = doc["wikitext"]
                tables = [t for t in parse_wikitables(doc["wikitext"])
                          if len(t["rows"]) > 1 and _is_poll_table(t)]
                out[key] = [{"heading": t["heading"], "rows": t["rows"][: self.max_rows_per_table]}
                            for t in tables[: self.max_tables_per_page]]
                attribution["pages"][key] = {
                    "title": title, "revid": doc.get("revid"),
                    "url": f"https://en.wikipedia.org/w/index.php?title="
                           f"{urllib.parse.quote(title.replace(' ', '_'))}&oldid={doc.get('revid')}",
                }
            except Exception as exc:  # noqa: BLE001 — per-page degradation
                errors.append(f"{key}: {type(exc).__name__}: {exc}")
        if not out:
            return Section(self.name, "unavailable", self.licence, attribution,
                           reason="; ".join(errors) or "no pages")
        sec = Section(self.name, "ok", self.licence, attribution, data=out,
                      reason="; ".join(errors) or None, raw=json.dumps(raws))
        return sec

    def render(self, section: Section, budget: int) -> str:
        if section.status != "ok":
            return f"## Polls (Wikipedia)\n\n_unavailable: {section.reason}_\n"
        lines = ["## Polls (Wikipedia, CC BY-SA 4.0; snapshot of the day before)\n"]
        used = 0
        for key, tables in section.data.items():
            if not tables:
                continue
            lines.append(f"### {key}\n")
            for t in tables:
                block = [f"**{t['heading']}**"]
                for r in t["rows"]:
                    block.append("| " + " | ".join(c[:40] for c in r[:9]) + " |")
                text = "\n".join(block) + "\n"
                if used + len(text) > budget:
                    lines.append("_…truncated to budget…_\n")
                    return "\n".join(lines)
                lines.append(text)
                used += len(text)
        return "\n".join(lines)


# --------------------------------------------------------------------------- markets

POLYMARKET_API = "https://gamma-api.polymarket.com/markets"
KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2/markets"


@dataclass
class PredictionMarkets:
    """Prices for questions mapped in a `markets.json`:
    {"<question_id>": {"polymarket_slug": "...", "polymarket_outcome": "Democratic",
                       "kalshi_ticker": "..."}}"""
    mapping: dict[str, dict[str, str]]
    name: str = "prediction_markets"
    licence: str = "platform terms (see legal-review §2.4)"

    def fetch(self, http: Http, day: date) -> Section:
        data: dict[str, Any] = {}
        raws: dict[str, Any] = {}
        errors: list[str] = []
        if not self.mapping:
            return Section(self.name, "unavailable", self.licence, {},
                           reason="no market mapping configured")
        for qid, m in sorted(self.mapping.items()):
            entry: dict[str, Any] = {}
            if m.get("polymarket_slug"):
                try:
                    txt = http(POLYMARKET_API, {"slug": m["polymarket_slug"]})
                    raws[f"polymarket:{qid}"] = txt
                    mk = json.loads(txt)
                    mk = mk[0] if isinstance(mk, list) else mk
                    outcomes = json.loads(mk.get("outcomes", "[]")) if isinstance(
                        mk.get("outcomes"), str) else mk.get("outcomes", [])
                    prices = json.loads(mk.get("outcomePrices", "[]")) if isinstance(
                        mk.get("outcomePrices"), str) else mk.get("outcomePrices", [])
                    entry["polymarket"] = {o: float(p) for o, p in zip(outcomes, prices)}
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"polymarket {qid}: {type(exc).__name__}: {exc}")
            if m.get("kalshi_ticker"):
                try:
                    txt = http(f"{KALSHI_API}/{m['kalshi_ticker']}", None)
                    raws[f"kalshi:{qid}"] = txt
                    mk = json.loads(txt).get("market", {})
                    lp = mk.get("last_price")
                    entry["kalshi"] = {"yes_last": (float(lp) / 100.0) if lp is not None else None,
                                       "yes_bid": mk.get("yes_bid"), "yes_ask": mk.get("yes_ask")}
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"kalshi {qid}: {type(exc).__name__}: {exc}")
            if entry:
                data[qid] = entry
        if not data:
            return Section(self.name, "unavailable", self.licence, {"mapping": self.mapping},
                           reason="; ".join(errors) or "no prices")
        return Section(self.name, "ok", self.licence, {"mapping": self.mapping,
                       "platforms": ["Polymarket (Gamma API)", "Kalshi (public API)"]},
                       data=data, reason="; ".join(errors) or None, raw=json.dumps(raws))

    def render(self, section: Section, budget: int) -> str:
        if section.status != "ok":
            return f"## Prediction markets\n\n_unavailable: {section.reason}_\n"
        lines = ["## Prediction-market prices (previous day's close; informational)\n",
                 "| question | Polymarket | Kalshi (yes) |", "|---|---|---|"]
        for qid, e in section.data.items():
            pm = ", ".join(f"{k}: {v:.2f}" for k, v in e.get("polymarket", {}).items())
            ks = e.get("kalshi", {}).get("yes_last")
            lines.append(f"| {qid} | {pm or '—'} | {ks if ks is None else f'{ks:.2f}'} |")
        return "\n".join(lines)[:budget] + "\n"


# --------------------------------------------------------------------------- GDELT

GDELT_API = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_QUERIES = [
    '"midterm elections" sourcelang:english',
    '"Senate race" sourcelang:english',
    '"governor race" OR "gubernatorial" sourcelang:english',
]


@dataclass
class GdeltHeadlines:
    queries: list[str] = field(default_factory=lambda: list(GDELT_QUERIES))
    max_per_query: int = 40
    pause_s: float = 6.0        # GDELT rate-limits to roughly one request per 5 s
    name: str = "gdelt_headlines"
    licence: str = "GDELT terms (free, attribution)"

    def fetch(self, http: Http, day: date) -> Section:
        items: list[dict[str, str]] = []
        raws: dict[str, str] = {}
        errors: list[str] = []
        seen: set[str] = set()
        for i, q in enumerate(self.queries):
            if i and self.pause_s:
                time.sleep(self.pause_s)
            try:
                params = {"query": q, "mode": "ArtList", "format": "json",
                          "maxrecords": str(self.max_per_query), "timespan": "24h",
                          "sort": "DateDesc"}
                txt = _retry(partial(http, GDELT_API, params), pause_s=self.pause_s)
                raws[q] = txt
                for a in json.loads(txt).get("articles", []):
                    url = a.get("url", "")
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    items.append({"title": a.get("title", "").strip(), "url": url,
                                  "domain": a.get("domain", ""), "seen": a.get("seendate", "")})
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{q!r}: {type(exc).__name__}: {exc}")
        if not items:
            return Section(self.name, "unavailable", self.licence, {"queries": self.queries},
                           reason="; ".join(errors) or "no articles")
        items.sort(key=lambda a: (a["seen"], a["url"]), reverse=True)
        return Section(self.name, "ok", self.licence, {"queries": self.queries,
                       "source": "GDELT 2.0 DOC API"}, data=items,
                       reason="; ".join(errors) or None, raw=json.dumps(raws))

    def render(self, section: Section, budget: int) -> str:
        if section.status != "ok":
            return f"## Headlines\n\n_unavailable: {section.reason}_\n"
        lines = ["## Headlines (GDELT, previous 24 h; titles and links only)\n"]
        used = len(lines[0])
        for a in section.data:
            s = f"- {a['title']} ({a['domain']}, {a['seen'][:8]})"
            if used + len(s) > budget:
                break
            lines.append(s)
            used += len(s)
        return "\n".join(lines) + "\n"


def _retry(fn, *, tries: int = 3, pause_s: float = 6.0):
    last: Exception | None = None
    for i in range(tries):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — retried, then surfaced by the caller
            last = exc
            if i < tries - 1 and pause_s:
                time.sleep(pause_s * (i + 1))
    assert last is not None
    raise last


# --------------------------------------------------------------------------- build

def default_sources(markets_mapping: dict[str, dict[str, str]] | None = None,
                    questions: list[dict[str, Any]] | None = None) -> list[Source]:
    polls = WikipediaPolls.from_questions(questions) if questions else WikipediaPolls()
    return [polls, PredictionMarkets(markets_mapping or {}), GdeltHeadlines()]


def build(day: date, out_dir: Path, *, sources: list[Source], http: Http = urllib_http,
          season: str = "", budget_chars: int = DEFAULT_BUDGET_CHARS,
          now: datetime | None = None) -> dict[str, Any]:
    """Fetch every source, write the packet, raw snapshots, attribution and hash index.
    Returns the index."""
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(exist_ok=True)
    sections: list[Section] = []
    for src in sources:
        try:
            sec = src.fetch(http, day)
        except Exception as exc:  # noqa: BLE001 — a source must never block the round
            sec = Section(src.name, "unavailable", src.licence, {},
                          reason=f"{type(exc).__name__}: {exc}")
        sections.append(sec)
        if sec.raw is not None:
            (raw_dir / f"{sec.name}.json").write_text(sec.raw)

    per_source_budget = budget_chars // max(len(sections), 1)
    packet = {
        "schema": SCHEMA, "season": season, "day": day.isoformat(),
        "licence": PACKET_LICENCE,
        "note": "Information available on the day before `day`. Identical for every entry. "
                "Forecasts produced from it are AI model outputs, not statements of fact.",
        "sections": [{"name": s.name, "status": s.status, "reason": s.reason, "data": s.data}
                     for s in sections],
    }
    md = [f"# Information packet — {season} — {day.isoformat()}\n",
          "Snapshot of public information as of the end of the previous day (UTC). ",
          "Every model receives exactly this text.\n"]
    for src, sec in zip(sources, sections):
        md.append(src.render(sec, per_source_budget))
    packet_json = C.canonical_json(packet) + b"\n"
    packet_md = ("\n".join(md)).encode()
    (out_dir / "packet.json").write_bytes(packet_json)
    (out_dir / "packet.md").write_bytes(packet_md)
    attribution = {
        "packet_licence": PACKET_LICENCE,
        "sources": [{"name": s.name, "status": s.status, "licence": s.licence,
                     "attribution": s.attribution} for s in sections],
    }
    (out_dir / "attribution.json").write_text(json.dumps(attribution, indent=2) + "\n")
    index = {
        "schema": SCHEMA, "day": day.isoformat(), "season": season,
        "built_at": (now or datetime.now(UTC)).isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "packet.json": hashlib.sha256(packet_json).hexdigest(),
        "packet.md": hashlib.sha256(packet_md).hexdigest(),
        "attribution.json": hashlib.sha256((out_dir / "attribution.json").read_bytes()).hexdigest(),
        "raw": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(raw_dir.glob("*.json"))},
        "sections": {s.name: s.status for s in sections},
    }
    (out_dir / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    return index
