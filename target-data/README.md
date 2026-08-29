# target-data

`vintages/<source>/<YYYY-MM-DD>.csv|parquet` — daily snapshots of every truth source,
hashed and recorded in the round manifest. Questions declare a `vintage_rule`
(`first_release` | `value_as_of(T_final)` | `final_revision`) that picks which snapshot resolves them.

`synthetic-hosp/` is generated data for the scaffold round `2026-w37` only (`make data`). It is
not real surveillance data.

Layout of a vintage file: `location, date, value` (long format).
