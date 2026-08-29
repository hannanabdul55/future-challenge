"""Build a static leaderboard from eval outputs (DESIGN.md §11).

Reads  <eval_dir>/relative_skill.csv, contribution.csv, scores.csv
Writes a single self-contained HTML file with rel skill + 95 % CI, coverage, contribution.
"""

from __future__ import annotations

import html
from pathlib import Path

import pandas as pd

CSS = """
body{font-family:system-ui,sans-serif;margin:2rem auto;max-width:900px;color:#171a21;background:#f5f6f8}
table{border-collapse:collapse;width:100%;font-size:.9rem}th,td{padding:.45rem .6rem;border-bottom:1px solid #d9dce3;text-align:left}
th{font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;color:#7a8194}
td.num{font-variant-numeric:tabular-nums;text-align:right}.baseline{color:#7a8194}
"""


def build(eval_dir: Path, out: Path) -> None:
    rs = pd.read_csv(eval_dir / "relative_skill.csv", index_col=0)
    contrib = pd.read_csv(eval_dir / "contribution.csv", index_col=0).iloc[:, 0]
    scores = pd.read_csv(eval_dir / "scores.csv")
    n_total = scores["question_id"].nunique()
    rs["coverage"] = rs["n_questions"] / n_total
    rs["contribution"] = contrib.reindex(rs.index)
    rs = rs.sort_values("rel_skill")

    rows = []
    for i, (m, r) in enumerate(rs.iterrows(), 1):
        cls = ' class="baseline"' if m == "fc-baseline" else ""
        rows.append(
            f"<tr{cls}><td class=num>{i}</td><td>{html.escape(str(m))}</td>"
            f"<td class=num>{r.rel_skill:.3f}</td><td class=num>{r.lo95:.3f} – {r.hi95:.3f}</td>"
            f"<td class=num>{r.coverage:.0%}</td><td class=num>{r.contribution:+.3f}</td>"
            f"<td class=num>{int(r.n_questions)}</td></tr>"
        )
    doc = f"""<!doctype html><meta charset=utf-8><title>Future Challenge leaderboard</title>
<style>{CSS}</style>
<h1>Future Challenge — leaderboard</h1>
<p>Relative skill = pairwise geometric-mean WIS ratio ÷ baseline (lower is better; &lt; 1 beats baseline).
Intervals are 95 % bootstrap over questions. Contribution = Δ ensemble score when the model is removed (positive adds information).</p>
<table><tr><th>#</th><th>Model</th><th>Rel. skill</th><th>95 % CI</th><th>Coverage</th><th>Contribution</th><th>N</th></tr>
{''.join(rows)}
</table>
<p>{n_total} resolved questions.</p>
"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc)


