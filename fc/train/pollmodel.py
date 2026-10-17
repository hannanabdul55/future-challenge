"""Poll-margin features and the probit target model (D-05, RESEARCH "Poll-Margin Model").

Features are read from the **rendered** packet rows (after the live renderer truncated cells
to 32 characters and rows to nine cells), never from raw wikitext, so a feature is used only
if the language model could have seen it. Parsing is plain string handling: no eval, no
template execution (T-04.1-05).

Targets use the packet-only prior of locked D-05: with ``prior_mode`` "packet" or "mask" an
unpolled race gets ``params["prior_const_<office>"]`` (0 by default), which uses nothing the
packet does not show. ``lean`` mode uses an outside state-lean feature and is usable only
under a dated D-05 amendment (plan 04.1-01); the author kept D-05 literal, so it is not used.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

PRIOR_MODES = ("packet", "lean", "mask")

# Pre-fit values from RESEARCH (sigma about 5-6 points); plan 04.1-07 writes fitted values
# through the same interface.
DEFAULT_PARAMS: dict[str, float | str] = {
    "fit": "none", "kappa": 0.0, "n0": 2.0, "b": 0.0,
    "sigma_senate": 6.0, "sigma_gov": 6.0, "p_other": 0.01,
}

P_MIN, P_MAX = 0.02, 0.98  # shrinkage cap on P(D) before rounding

_NUM = re.compile(r"^\s*(\d+(?:\.\d+)?)")
_THIRD_TAG = re.compile(r"\(([A-Z])\)")


@dataclass(frozen=True)
class Features:
    qid: str
    n_rows: int
    m_bar: float          # mean of D minus R, in points, over rows with both numbers
    layout: str           # "polls", "aggregator" or "none"
    unpolled: bool
    other_leader: bool    # a third-party column beats both D and R in the mean


def _cells(line: str) -> list[str]:
    body = line.strip().removeprefix("|").removesuffix("|")
    return [c.strip() for c in body.split(" | ")]


def _block(packet_md: str, qid: str) -> list[list[str]] | None:
    """Rows (header first) of the ``### {qid} — ...`` block, or None when absent."""
    lines = packet_md.splitlines()
    head = f"### {qid} "
    for i, line in enumerate(lines):
        if line.startswith(head):
            rows: list[list[str]] = []
            for nxt in lines[i + 1:]:
                if not nxt.startswith("|"):
                    break
                rows.append(_cells(nxt))
            return rows
    return None


def _num(cell: str) -> float | None:
    m = _NUM.match(cell)
    return float(m.group(1)) if m else None


def _party_cols(header: list[str], tag: str, word: str) -> list[int]:
    return [i for i, h in enumerate(header)
            if f"({tag})" in h or word in h.lower()]


def extract_features(packet_md: str, qid: str) -> Features:
    """Features of race ``qid`` from the rendered packet text."""
    rows = _block(packet_md, qid)
    none = Features(qid, 0, 0.0, "none", True, False)
    if not rows or len(rows) < 2:
        return none
    header, body = rows[0], rows[1:]
    layout = "aggregator" if "aggregation" in header[0].lower() else "polls"
    d_cols = _party_cols(header, "D", "democrat")
    r_cols = _party_cols(header, "R", "republican")
    if len(d_cols) != 1 or len(r_cols) != 1:
        return Features(qid, 0, 0.0, layout, True, False)
    di, ri = d_cols[0], r_cols[0]
    # Third-party columns carry a one-letter party tag such as (I); "Margin" is never read.
    third = [i for i, h in enumerate(header)
             if i not in (di, ri) and set(_THIRD_TAG.findall(h)) - {"D", "R"}]
    margins: list[float] = []
    d_vals: list[float] = []
    r_vals: list[float] = []
    t_vals: list[list[float]] = [[] for _ in third]
    for row in body:
        if max(di, ri) >= len(row):
            continue
        d, r = _num(row[di]), _num(row[ri])
        if d is None or r is None:
            continue
        margins.append(d - r)
        d_vals.append(d)
        r_vals.append(r)
        for k, ti in enumerate(third):
            if ti < len(row) and (t := _num(row[ti])) is not None:
                t_vals[k].append(t)
    if not margins:
        return Features(qid, 0, 0.0, layout, True, False)
    d_mean = sum(d_vals) / len(d_vals)
    r_mean = sum(r_vals) / len(r_vals)
    other = any(v and sum(v) / len(v) > max(d_mean, r_mean) for v in t_vals)
    return Features(qid, len(margins), sum(margins) / len(margins), layout, False, other)


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def race_probs(feat: Features, params: dict[str, float | str], *, office: str,
               lean: float = 0.0, prior_mode: str = "packet") -> dict[str, float]:
    """P(D), P(R), P(Other) for one race. Unrounded; ``sft_data.round_probs`` rounds."""
    if prior_mode not in PRIOR_MODES:
        raise ValueError(f"prior_mode {prior_mode!r} not in {PRIOR_MODES}")
    n0 = float(params["n0"])
    p_other = float(params["p_other"])
    w = 0.0 if feat.unpolled else feat.n_rows / (feat.n_rows + n0)
    if prior_mode == "lean":
        prior = float(params["kappa"]) * lean
    else:
        prior = float(params.get(f"prior_const_{office}", 0.0))
    sigma = float(params["sigma_senate"] if office == "senate" else params["sigma_gov"])
    m = feat.m_bar if not feat.unpolled else 0.0
    z = (w * m + (1.0 - w) * prior - float(params["b"])) / sigma
    p_d = min(max(_phi(z) * (1.0 - p_other), P_MIN), P_MAX)
    return {"D": p_d, "R": 1.0 - p_d - p_other, "Other": p_other}
