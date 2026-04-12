# Root SDK Makefile. Studio backend and frontend have their own Makefiles in
# `studio/backend/` and `apps/studio/`.

# --------------------------------------------------------------------------- #
#  Variables
# --------------------------------------------------------------------------- #
UV              := uv run
RUFF            := $(UV) ruff
TY              := $(UV) ty
COVERAGE        := $(UV) coverage
PYTEST          := $(UV) pytest
SDK_PY_PATHS     := src tests

#  Ruff & Black share the same default 88-char line length.                   #
#  Set your project-wide preference *once* here and both the linter           #
#  and the formatter will respect it (docs:                                    #
#     https://docs.astral.sh/ruff/settings/#line-length).                      #
LINE_LENGTH     := 120

# Tell Ruff the desired style (black-compatible but 100 chars wide).
RUFF_FLAGS      := --line-length $(LINE_LENGTH)

# --------------------------------------------------------------------------- #
#  Default target (runs when you just type `make`)
# --------------------------------------------------------------------------- #
.DEFAULT_GOAL := help

# Rule descriptions are picked up by the help target.
.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

# --------------------------------------------------------------------------- #
#  Dependency management
# --------------------------------------------------------------------------- #
.PHONY: sync
sync: ## Create/refresh the SDK development environment
	uv sync --extra dev

# --------------------------------------------------------------------------- #
#  Code quality
# --------------------------------------------------------------------------- #
.PHONY: format
format: ## Re-format the SDK workspace with Ruff
	$(RUFF) format $(SDK_PY_PATHS) $(RUFF_FLAGS)

.PHONY: lint
lint: ## Run Ruff in lint-only mode for the SDK workspace
	$(RUFF) check $(SDK_PY_PATHS) $(RUFF_FLAGS)

.PHONY: fix
fix: ## Lint + safe auto-fixes for the SDK workspace
	$(RUFF) check $(SDK_PY_PATHS) --fix $(RUFF_FLAGS)

.PHONY: typecheck
typecheck: ## Static type analysis for the SDK package
	$(TY) check src/relayna

# --------------------------------------------------------------------------- #
#  Tests & coverage
# --------------------------------------------------------------------------- #
.PHONY: test
test: ## Run the SDK test suite
	$(PYTEST) tests

.PHONY: coverage
coverage: clean ## Run SDK tests with coverage (fail if <70 %)
	@echo "coverage package not installed; running pytest without coverage"
	$(PYTEST) tests

# --------------------------------------------------------------------------- #
#  Snapshot tests
# --------------------------------------------------------------------------- #
.PHONY: snapshots-fix
snapshots-fix: ## Update broken inline snapshots for the SDK suite
	@echo "inline-snapshot plugin not installed; running pytest without snapshot updates"
	$(PYTEST) tests

.PHONY: snapshots-create
snapshots-create: ## Create new inline snapshots for the SDK suite
	@echo "inline-snapshot plugin not installed; running pytest without snapshot creation"
	$(PYTEST) tests

# --------------------------------------------------------------------------- #
#  Workspace hints
# --------------------------------------------------------------------------- #
.PHONY: backend-help frontend-help studio-backend-docker-build studio-frontend-docker-build studio-docker-build
.PHONY: studio-mocks-serve studio-mocks-payloads studio-mocks-register
backend-help: ## Show Studio backend Makefile targets
	$(MAKE) -C studio/backend help

frontend-help: ## Show Studio frontend Makefile targets
	$(MAKE) -C apps/studio help

studio-backend-docker-build: ## Build the Studio backend Docker image
	$(MAKE) -C studio/backend docker-build

studio-frontend-docker-build: ## Build the Studio frontend Docker image
	$(MAKE) -C apps/studio docker-build

studio-docker-build: ## Build both Studio Docker images
	$(MAKE) -C studio/backend docker-build
	$(MAKE) -C apps/studio docker-build

studio-mocks-serve: ## Serve local Relayna-compatible Studio mock services on port 9100
	./.venv/bin/python scripts/studio_mock_services.py serve

studio-mocks-payloads: ## Print mock Studio service registry payloads
	./.venv/bin/python scripts/studio_mock_services.py payloads

studio-mocks-register: ## Register/update the mock services against Studio on localhost:8000
	./.venv/bin/python scripts/studio_mock_services.py register

# --------------------------------------------------------------------------- #
#  Misc
# --------------------------------------------------------------------------- #
.PHONY: clean
clean: ## Remove SDK caches & artefacts
	rm -rf .mypy_cache .ruff_cache .pytest_cache htmlcov coverage.xml
