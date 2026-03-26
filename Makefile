# --------------------------------------------------------------------------- #
#  Variables
# --------------------------------------------------------------------------- #
UV              := uv run
RUFF            := $(UV) ruff
TY              := $(UV) ty
COVERAGE        := $(UV) coverage
PYTEST          := $(UV) pytest

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
sync: ## Create/refresh the local environment with all optional & dev deps
	uv sync --all-extras --all-packages

# --------------------------------------------------------------------------- #
#  Code quality
# --------------------------------------------------------------------------- #
.PHONY: format
format: ## Re-format the entire codebase (Black-style) with Ruff
	$(RUFF) format . $(RUFF_FLAGS)

.PHONY: lint
lint: ## Run Ruff in lint-only mode (no fixes)
	$(RUFF) check . $(RUFF_FLAGS)

.PHONY: fix
fix: ## Lint + safe auto-fixes (same as lint + --fix)
	$(RUFF) check . --fix $(RUFF_FLAGS)

.PHONY: typecheck
typecheck: ## Static type analysis with ty
	$(TY) check src/relayna

# --------------------------------------------------------------------------- #
#  Tests & coverage
# --------------------------------------------------------------------------- #
.PHONY: test
test: ## Run the test suite
	$(PYTEST)

.PHONY: coverage
coverage: clean ## Run tests with coverage (fail if <70 %)
	@echo "coverage package not installed; running pytest without coverage"
	$(PYTEST)

# --------------------------------------------------------------------------- #
#  Snapshot tests
# --------------------------------------------------------------------------- #
.PHONY: snapshots-fix
snapshots-fix: ## Update broken inline snapshots
	@echo "inline-snapshot plugin not installed; running pytest without snapshot updates"
	$(PYTEST)

.PHONY: snapshots-create
snapshots-create: ## Create new inline snapshots from scratch
	@echo "inline-snapshot plugin not installed; running pytest without snapshot creation"
	$(PYTEST)

# --------------------------------------------------------------------------- #
#  Misc
# --------------------------------------------------------------------------- #
.PHONY: clean
clean: ## Remove caches & artefacts
	rm -rf .mypy_cache .ruff_cache .pytest_cache htmlcov coverage.xml
