SYMBOL       ?= ES
DATA_SRC     ?= .build/databento
DATA_OUT     ?= .build/magsi/data
TUI_TF       ?= 30m
TUI_FROM     ?= 2025-12-22
TUI_TO       ?= 2026-01-08

.PHONY: data
data:
	uv run --project src/datasmith datasmith ingest databento $(SYMBOL) --source $(DATA_SRC) --out $(DATA_OUT)

.PHONY: tui
tui:
	uv run --project src/tui sikap-tui --parquet $(DATA_OUT)/$(SYMBOL).parquet --tf $(TUI_TF) \
		$(if $(TUI_FROM),--from $(TUI_FROM)) \
		$(if $(TUI_TO),--to $(TUI_TO))

.PHONY: test
test:
	cd src/core      && uv run pytest -q
	cd src/datasmith && uv run pytest -q
	cd src/tui       && uv run pytest -q
