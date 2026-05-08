SYMBOL       ?= NQ
DATA_SRC     ?= .build/databento
DATA_OUT     ?= .build/magsi/data
TUI_TF       ?= 15m

.PHONY: data
data:
	uv run --project src/datasmith datasmith ingest databento $(SYMBOL) --source $(DATA_SRC) --out $(DATA_OUT)

.PHONY: tui
tui:
	uv run --project src/tui sikap-tui --parquet $(DATA_OUT)/$(SYMBOL).parquet --tf $(TUI_TF)

.PHONY: test
test:
	cd src/core      && uv run pytest -q
	cd src/datasmith && uv run pytest -q
	cd src/tui       && uv run pytest -q
