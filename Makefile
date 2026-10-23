UV := $(shell command -v uv 2>/dev/null)

.PHONY: dev auth status lint install publish _check_uv

install:
	bash scripts/install.py

dev: _check_uv
	$(UV) sync --extra dev

auth: _check_uv
	$(UV) run google-mcp auth

status: _check_uv
	$(UV) run google-mcp status

lint: _check_uv
	$(UV) run --extra dev ruff check .
	$(UV) run --extra dev ruff format --check .
	$(UV) run --extra dev ty check src/

publish: _check_uv
	$(UV) run --script scripts/publish.py

_check_uv:
ifndef UV
	$(error uv not found. Install it from https://docs.astral.sh/uv/getting-started/installation/)
endif
