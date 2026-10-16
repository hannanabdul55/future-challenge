"""Static season board (BOARD-01..05) rendered from eval/out/<season>/board.json.

One self-contained HTML file: per-tier tables with relative skill and CIs, provisional
flags, calibration summary, trajectory charts (inline SVG, no JS dependencies), and links
from every entry to its identity record, receipt and daily run bundles.
"""

from __future__ import annotations

import html
import json
import math
from pathlib import Path

CSS = """
:root{--fg:#171a21;--bg:#f7f7f9;--mut:#6b7280;--line:#dcdfe6;--acc:#2563eb;--acc2:#dc2626;--mk:#111}
@media(prefers-color-scheme:dark){:root{--fg:#e7e9ee;--bg:#0f1116;--mut:#9aa3b2;--line:#2a2f3a;--acc:#60a5fa;--acc2:#f87171;--mk:#eee}}
body{font-family:system-ui,sans-serif;margin:2rem auto;max-width:1000px;padding:0 16px;color:var(--fg);background:var(--bg);line-height:1.45}
table{border-collapse:collapse;width:100%;font-size:.9rem;margin:.5rem 0 1.5rem}th,td{padding:.4rem .6rem;border-bottom:1px solid var(--line);text-align:left}
th{font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;color:var(--mut)}td.num{font-variant-numeric:tabular-nums;text-align:right}
.prov{color:var(--mut);font-style:italic}.note{color:var(--mut);font-size:.85rem}
svg{max-width:100%;height:auto;background:transparent}.leg span{display:inline-block;margin-right:1rem}
"""


def _svg_lines(series: dict[str, list[tuple[int, float]]], n_days: int, *, ylab: str,
               ylim=(0.0, 1.0), w=640, h=220) -> str:
    pad = 36
    x = lambda i: pad + (w - 2 * pad) * (i / max(n_days - 1, 1))
    y = lambda v: h - pad - (h - 2 * pad) * ((v - ylim[0]) / (ylim[1] - ylim[0]))
    palette = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#0891b2", "#be185d"]
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{html.escape(ylab)} by day">']
    for t in (ylim[0], (ylim[0] + ylim[1]) / 2, ylim[1]):
        out.append(f'<line x1="{pad}" y1="{y(t):.1f}" x2="{w - pad}" y2="{y(t):.1f}" '
                   f'stroke="var(--line)"/><text x="4" y="{y(t) + 4:.1f}" font-size="10" '
                   f'fill="var(--mut)">{t:g}</text>')
    legend = []
    for k, (name, pts) in enumerate(sorted(series.items())):
        col = "var(--mk)" if name == "fc-market" else palette[k % len(palette)]
        dash = ' stroke-dasharray="4 3"' if name.startswith("fc-") or name == "hub-ensemble" else ""
        path = " ".join(f"{'M' if i == 0 else 'L'}{x(d):.1f},{y(v):.1f}" for i, (d, v) in enumerate(pts)
                        if not math.isnan(v))
        if path:
            out.append(f'<path d="{path}" fill="none" stroke="{col}" stroke-width="2"{dash}/>')
        legend.append(f'<span style="color:{col}">— {html.escape(name)}</span>')
    out.append("</svg>")
    return "".join(out) + f'<div class="leg note">{"".join(legend)}</div>'


def build(board_json: Path, out: Path) -> None:
    b = json.loads(board_json.read_text())
    days = b["days"]
    idx = {d: i for i, d in enumerate(days)}
    parts = [("<!doctype html><meta charset=utf-8>"
              "<meta name=viewport content='width=device-width,initial-scale=1'>"
              f"<title>Future Challenge — {html.escape(b['season'])}</title><style>{CSS}</style>"),
             f"<h1>Future Challenge — {html.escape(b['season'])}</h1>",
             ("<p class=note>Forecasts on this page are AI model outputs on a fixed daily "
              "information packet. They are not statements of fact or endorsements. "
              "Lower score is better; relative skill &lt; 1 beats the persistence baseline. "
              "Intervals are 95 % block bootstrap by day. Entries with fewer than "
              f"{b['min_days_for_headline']} scored days are provisional.</p>"),
             (f"<p class=note>{len(days)} daily rounds · {b['n_questions']} questions · "
              f"{b['n_resolved']} resolved.</p>")]

    rel = b["relative_skill"]
    for tier, label in (("S", "Tier S — hub-run, frozen open weights"),
                        ("A", "Tier A — self-run, audited"), ("H", "Reference baselines")):
        rows = [r for r in rel if r["tier"] == tier]
        if not rows:
            continue
        rows.sort(key=lambda r: r["rel_skill"])
        parts.append(f"<h2>{html.escape(label)}</h2><table><tr><th>#</th><th>Entry</th>"
                     "<th>Rel. skill</th><th>95 % CI</th><th>Days</th><th>N</th></tr>")
        for i, r in enumerate(rows, 1):
            cls = ' class=prov' if r.get("provisional") else ""
            parts.append(f"<tr{cls}><td class=num>{i}</td><td>{html.escape(r['model_id'])}"
                         f"{' (provisional)' if r.get('provisional') else ''}</td>"
                         f"<td class=num>{r['rel_skill']:.3f}</td>"
                         f"<td class=num>{r['lo95']:.3f} – {r['hi95']:.3f}</td>"
                         f"<td class=num>{int(r['n_days'])}</td><td class=num>{int(r['n_questions'])}</td></tr>")
        parts.append("</table>")

    tr = b["trajectories"]
    if tr and days:
        for key, ylab, ylim in (("p_house_d", "P(Democratic House majority)", (0, 1)),
                                ("p_senate_d", "P(Democratic caucus ≥ 51 Senate seats)", (0, 1)),
                                ("seats_house_d_median", "Median Democratic House seats", (180, 260)),
                                ("seats_senate_dcaucus_median", "Median Democratic-caucus Senate seats", (40, 56))):
            series: dict[str, list[tuple[int, float]]] = {}
            for r in tr:
                v = r.get(key)
                if v is None or r["day"] not in idx:
                    continue
                series.setdefault(r["model_id"], []).append((idx[r["day"]], float(v)))
            series = {k: sorted(v) for k, v in series.items() if k != "fc-uniform"}
            if series:
                parts.append(f"<h3>{html.escape(ylab)}</h3>" + _svg_lines(series, len(days), ylab=ylab, ylim=ylim))

    cal = b["calibration"]
    if cal:
        parts.append("<h2>Calibration</h2><table><tr><th>Entry</th><th>Brier</th><th>n probs</th>"
                     "<th>50 % cov.</th><th>95 % cov.</th><th>n quantile</th></tr>")
        def fmt(r, k):
            return "—" if r.get(k) is None else f"{r[k]:.3f}"

        for r in sorted(cal, key=lambda r: r["model_id"]):
            def f(k, r=r):
                return fmt(r, k)
            parts.append(f"<tr><td>{html.escape(r['model_id'])}</td><td class=num>{f('brier')}</td>"
                         f"<td class=num>{r.get('n_probs', 0)}</td><td class=num>{f('coverage50')}</td>"
                         f"<td class=num>{f('coverage95')}</td><td class=num>{r.get('n_quantile', 0)}</td></tr>")
        parts.append("</table>")

    parts.append("<h2>Entries</h2><table><tr><th>Entry</th><th>Tier</th><th>Days</th><th>Artifacts</th></tr>")
    for m, e in b["entries"].items():
        links = (f'<a href="../model-metadata/{html.escape(m)}.yml">identity</a> · '
                 f'<a href="../sidecar/{html.escape(m)}/">receipts</a> · '
                 f'<a href="../runs/{html.escape(b["season"])}/">runs</a> · '
                 f'<a href="../model-output/{html.escape(m)}/">forecasts</a>')
        parts.append(f"<tr><td>{html.escape(m)}</td><td>{html.escape(str(e['tier']))}</td>"
                     f"<td class=num>{len(e['days'])}</td><td class=note>{links}</td></tr>")
    parts.append("</table>")
    parts.append("<h2>Rounds</h2><p class=note>" + " · ".join(
        f'<a href="../rounds/{html.escape(b["season"])}/{d}/manifest.json">{d}</a>' for d in days) + "</p>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts) + "\n")
