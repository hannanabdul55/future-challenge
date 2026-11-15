# Macro release template

Templated, mechanically resolvable questions on scheduled US macro releases. Code: `fc/macro.py`.
Data: `questions/templates/macro/series.csv` (one row per series) and `releases.csv` (one row per
scheduled release, from the BLS, BEA and Federal Reserve calendars).

## Why a template, and why this shape

One operator cannot author thirty checklist-clean questions a month by hand. A template makes the
marginal question free: one spec, one resolver, one entity list. The risk is correlation. "Will CPI
print above consensus?" twelve months running is one bit of information twelve times, because the
model with the right inflation prior wins every instance. The template fights that three ways:

- **Many series with different drivers.** Prices, labour, rates, growth and the policy decision
  move for different reasons on different days.
- **Seeded threshold placement.** The binary question's threshold is the last print plus ±1 or ±2
  steps, with sign and size drawn from `sha256(season|series|period)`. The surprise direction
  varies across questions, so no standing bias wins them all. Deterministic and auditable.
- **Quantile questions on the value itself.** One quantile question carries more information
  than one binary and is scored with WIS.

Correlation that remains is handled in scoring: every question carries
`cluster = macro/<release month>`, and relative skill uses a block bootstrap over clusters, not
days. The board must show the cluster-corrected effective n beside any rank.

## What is generated

For each release in a month (`fc questions macro generate --season S --month YYYY-MM`):

| series | kind | id pattern |
|---|---|---|
| each data series on the release | quantile on the first-published value, 9 levels, bounded | `macro-<series>-<period>` |
| same | binary "strictly greater than T" | `macro-<series>-<period>-gt` |
| FOMC | categorical: cut_50bp_or_more / cut_25bp / hold / hike | `macro-fomc-<period>` |

Each question records the `reference` it was built from (previous period's value, FRED vintage at
generation time), the `threshold` and `threshold_offset`, a declarative `baseline` (persistence
centre and historical spread for quantiles; the historical frequency of a move beyond the offset
for binaries), `track: ranked`, `cluster`, and a full resolution block with
`vintage_rule: first_release`, `expected_resolution_date` = the scheduled release date, the void
list, and `T_final` = release date + 21 days.

## Resolution

`fc questions macro resolve --questions questions/S/questions.json --out target-data/truth/S/truth.csv`

First print, as published. BLS v1 API first (key-free, official), FRED CSV as fallback. FOMC
resolves from the change in `DFEDTARU` across the meeting. A question whose release is more than
14 days late with no data is voided. Revisions are ignored: the first print is final.

`fc questions macro dry-run --series-id cpi-headline-yoy --months 12` replays the template over
the last twelve known periods with only the history before each one, then resolves against the
actual value. It is the same code path as the live resolver. Caveat: the replay uses FRED's
current vintage, not the historical first prints; for the series here the difference is small
(seasonal factors and payroll benchmark revisions) and is stated.

## Rolling season mechanics

- `Q.active_on(questions, day)` filters the daily set: a question is asked only while its
  `expected_resolution_date` is still in the future (the release day itself is excluded; CPI
  prints at 13:30 UTC, after the 12:00 UTC commit, but nobody should depend on that margin). The
  runner applies it before prompting; imputation only counts questions active on that day.
- `fc questions assemble --season S` merges `questions/S/world.json` (curated, `track: world`)
  with every `questions/S/macro/*.json` into `questions.json`. Only `ranked` questions enter
  relative skill. `world` questions are scored and displayed, never ranked, never imputed.
- Add a month: append the agency's dates to `releases.csv`, run `generate` for that month,
  `assemble`, and re-initialise the season manifest. The question hash changes; rounds opened
  before the change keep their own hash.

## Packet

When the season contains macro questions, the packet gains a `macro_series` section: the last
24 transformed observations of every series from FRED (current vintage, dated strictly before the
round day). Prediction-market prices are not included for these questions.

## Known limits (2026-11)

- US-only. Eurostat HICP flash, ONS CPI and the ECB/BoE decisions would decorrelate further and
  are the next rows to add; each needs a resolver source.
- BEA's 2027 calendar was not published at the time of writing, so no GDP-advance questions are
  generated yet; the series row exists.
- The BLS v1 API allows 25 requests per day without a key. Generation uses FRED; only the
  resolver touches BLS.
