"""Point-in-time Wikipedia for historical packets (D-03, RESEARCH Pattern 1).

``WikiCache.rev_at(title, asof)`` resolves the revision that was current at ``asof`` with one
MediaWiki ``prop=revisions`` call; ``WikiCache.wikitext(revid)`` fetches that exact revision's
wikitext with ``action=parse&oldid``. Both are cached on disk, so a rebuilt packet makes no
network call and the revision archive that CC BY-SA attribution points at is kept.

``historical_http`` wraps a cache as the ``Http`` callable ``fc.packet.WikipediaPolls.fetch``
expects, so the unchanged live renderer builds historical packets. It answers only
``action=parse`` on the Wikipedia API: markets and headlines can never reach the network from
a historical build (T-04.1-04).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fc import packet as P

from . import paths

USER_AGENT = ("future-challenge-history/0.1 "
              "(+https://github.com/future-challenge; historical poll packets, research use)")

_ASOF = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

Fetch = Callable[[dict[str, Any]], str]


def urllib_fetch(params: dict[str, Any], *, timeout: float = 30.0) -> str:
    """One real MediaWiki API call with the descriptive User-Agent."""
    url = f"{P.WIKI_API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or exc.code >= 500
    return isinstance(exc, urllib.error.URLError | TimeoutError | ConnectionError)


class WikiCache:
    def __init__(self, root: Path = paths.WIKI_CACHE, *, fetch: Fetch | None = None,
                 pause_s: float = 0.5, tries: int = 4,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.root = Path(root)
        self._fetch = fetch or urllib_fetch
        self.pause_s = pause_s
        self.tries = tries
        self._sleep = sleep
        self._called = False
        self.network_calls = 0

    # ----------------------------------------------------------------- transport

    def _call(self, params: dict[str, Any]) -> dict[str, Any]:
        """One uncached API call: paced, with bounded exponential backoff."""
        if self._called and self.pause_s:
            self._sleep(self.pause_s)
        self._called = True
        last: Exception | None = None
        for attempt in range(self.tries):
            try:
                self.network_calls += 1
                doc = json.loads(self._fetch(params))
                if isinstance(doc, dict) and "error" in doc:
                    raise RuntimeError(f"MediaWiki API error: {doc['error']}")
                return doc
            except Exception as exc:  # retried only when transient
                last = exc
                if not _retryable(exc) or attempt == self.tries - 1:
                    raise
                self._sleep(max(self.pause_s, 1.0) * 2 ** attempt)
        raise RuntimeError(f"unreachable: {last}")  # pragma: no cover

    def _read(self, path: Path) -> dict[str, Any] | None:
        if path.is_file():
            return json.loads(path.read_text())
        return None

    def _write(self, path: Path, doc: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
        tmp.replace(path)

    # ----------------------------------------------------------------- revisions

    def _asof_path(self, title: str, asof: str) -> Path:
        key = hashlib.sha1(f"{title}|{asof}".encode()).hexdigest()
        return self.root / "asof" / f"{key}.json"

    def rev_at(self, title: str, asof: str) -> dict[str, Any]:
        """The revision of ``title`` current at ``asof`` (UTC, ``YYYY-MM-DDTHH:MM:SSZ``).

        Returns {"title", "revid", "timestamp", "redirected_from"}. Raises LookupError when
        the page does not exist or has no revision at or before ``asof`` (a missing page is
        cached too, so deriving a race list is repeatable offline)."""
        if not _ASOF.match(asof):
            raise ValueError(f"asof must be YYYY-MM-DDTHH:MM:SSZ, got {asof!r}")
        path = self._asof_path(title, asof)
        rec = self._read(path)
        if rec is None:
            doc = self._call({
                "action": "query", "prop": "revisions", "titles": title, "rvlimit": 1,
                "rvstart": asof, "rvdir": "older", "rvprop": "ids|timestamp",
                "redirects": 1, "format": "json", "formatversion": 2})
            rec = self._record(title, asof, doc)
            self._write(path, rec)
        if rec.get("error"):
            raise LookupError(rec["error"])
        # Leakage guard (Pitfall 3, T-04.1-04): never hand back a revision after asof.
        if rec["timestamp"] > asof:
            raise AssertionError(
                f"revision {rec['revid']} of {rec['title']!r} is stamped {rec['timestamp']}, "
                f"after {asof}")
        return rec

    @staticmethod
    def _record(title: str, asof: str, doc: dict[str, Any]) -> dict[str, Any]:
        query = doc.get("query", {})
        pages = query.get("pages", [])
        if not pages:
            return {"error": f"no page returned for {title!r}"}
        page = pages[0]
        if page.get("missing") or page.get("invalid"):
            return {"error": f"page {title!r} does not exist at the time of the query"}
        revs = page.get("revisions") or []
        if not revs:
            return {"error": f"page {page.get('title')!r} has no revision at or before {asof}"}
        rev = revs[0]
        redirected_from = None
        for r in query.get("redirects", []):
            if r.get("to") == page["title"]:
                redirected_from = r.get("from")
        return {"title": page["title"], "revid": rev["revid"], "timestamp": rev["timestamp"],
                "redirected_from": redirected_from}

    # ----------------------------------------------------------------- wikitext

    def wikitext(self, revid: int) -> str:
        """Wikitext of exactly revision ``revid`` (the raw CC BY-SA archive)."""
        path = self.root / "rev" / f"{int(revid)}.json"
        rec = self._read(path)
        if rec is None:
            doc = self._call({"action": "parse", "oldid": int(revid), "prop": "wikitext",
                              "format": "json", "formatversion": 2})
            parse = doc["parse"]
            if int(parse["revid"]) != int(revid):
                raise AssertionError(f"asked for revision {revid}, got {parse['revid']}")
            rec = {"title": parse["title"], "revid": int(parse["revid"]),
                   "wikitext": parse["wikitext"]}
            self._write(path, rec)
        return rec["wikitext"]


def historical_http(cache: WikiCache, asof: str) -> P.Http:
    """An ``Http`` for ``WikipediaPolls.fetch`` that serves the revision current at ``asof``."""
    if not _ASOF.match(asof):
        raise ValueError(f"asof must be YYYY-MM-DDTHH:MM:SSZ, got {asof!r}")

    def http(url: str, params: dict[str, Any] | None = None) -> str:
        if url != P.WIKI_API or not params or params.get("action") != "parse" \
                or not params.get("page"):
            raise RuntimeError(f"historical build refuses {url!r} {params!r}: only "
                               "action=parse on the Wikipedia API is allowed")
        rev = cache.rev_at(params["page"], asof)
        text = cache.wikitext(rev["revid"])
        return json.dumps({"parse": {"title": rev["title"], "revid": rev["revid"],
                                     "wikitext": text}})

    return http
