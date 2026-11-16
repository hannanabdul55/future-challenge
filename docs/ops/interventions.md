# Operator intervention log

Every manual action that touches the daily hub-run job is logged here within 24 hours:
restarts, re-runs, edits on the GPU box, key changes, manual pushes, timer changes.

The evidence command (`uv run python -m fc.ops_evidence`) reads this file. A season day
listed in "Season day affected" does not count toward the 7-of-10 unattended-operation gate
(OPS-03), because the action tainted that day's evidence. Git history (author and time)
makes edits to this file visible; the weekly review reads it.

Rule: "Season day affected" lists comma-separated `YYYY-MM-DD` days whose evidence the
action taints (for example `2026-11-26, 2026-11-27`). Date and time are UTC.

| Date (UTC) | Time (UTC) | Season day affected | Action | Reason | By |
|---|---|---|---|---|---|

No interventions recorded.
