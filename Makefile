.PHONY: setup test data baseline validate score leaderboard manifest lint

setup:
	uv sync --extra dev

test:
	uv run pytest -q

data:
	uv run python scripts/make_synthetic_data.py

baseline: data
	uv run python scripts/make_baseline.py

validate:
	uv run fc validate model-output/fc-baseline/2026-w37.csv --tasks hub-config/tasks.json

score:
	uv run fc score --pred model-output/*/2026-w37.csv --truth target-data/vintages/synthetic-hosp/truth-2026-w37.csv --baseline fc-baseline --min-shared 5 --out eval/out

leaderboard: score
	uv run fc leaderboard --scores eval/out/scores.csv --out leaderboard/index.html

manifest:
	uv run python scripts/fill_manifest.py rounds/2026-w37/manifest.json

lint:
	uv run ruff check .
