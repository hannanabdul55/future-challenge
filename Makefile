.PHONY: setup test data baseline validate score leaderboard manifest lint questions season packet round-open image

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

# ---- Active Season (default: 2026-year-end; also supports 2026-us-midterms) ------------
SEASON ?= 2026-year-end
DAY ?= $(shell date -u +%F)

questions:
	@if [ "$(SEASON)" = "2026-year-end" ]; then \
	  uv run fc questions generate --season $(SEASON) --out questions/$(SEASON)/questions.json && \
	  uv run fc questions validate questions/$(SEASON)/questions.json --event-date 2026-12-31; \
	else \
	  uv run fc questions generate --races questions/$(SEASON)/races.csv --out questions/$(SEASON)/questions.json && \
	  uv run fc questions validate questions/$(SEASON)/questions.json --event-date 2026-11-03; \
	fi

season: questions
	@if [ "$(SEASON)" = "2026-year-end" ]; then \
	  uv run fc season init --season $(SEASON) --questions questions/$(SEASON)/questions.json \
	    --first-day 2026-11-10 --last-day 2026-12-31 --last-admission 2026-12-31T12:00:00Z; \
	else \
	  uv run fc season init --season $(SEASON) --questions questions/$(SEASON)/questions.json \
	    --first-day 2026-10-26 --last-day 2026-11-03 --last-admission 2026-11-03T12:00:00Z; \
	fi

packet:
	uv run fc packet build --date $(DAY) --out target-data/packets/$(SEASON)/$(DAY) --season $(SEASON) \
	  --markets questions/$(SEASON)/markets.json

round-open: packet
	uv run fc round open --season $(SEASON) --date $(DAY) --packet target-data/packets/$(SEASON)/$(DAY)

image:
	docker build -t fc-forecaster:2026.10 -f runner/forecast/Dockerfile .
