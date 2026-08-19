# Studio backend Makefile.

UV              := uv
RUN             := $(UV) run
RUFF            := $(RUN) ruff
PYTEST          := $(RUN) pytest
TY              := $(RUN) ty
BUILD           := $(UV) build
PIP_AUDIT       := $(RUN) --with pip-audit pip-audit
LINE_LENGTH     := 120
RUFF_FLAGS      := --line-length $(LINE_LENGTH)
PY_PATHS        := src tests
IMAGE_NAME      := relayna-studio-backend
ALEMBIC         := $(RUN) alembic -c alembic.ini

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

.PHONY: sync
sync: ## Create/refresh the Studio backend development environment
	$(UV) sync --extra dev

.PHONY: format
format: ## Re-format the Studio backend package with Ruff
	$(RUFF) format $(PY_PATHS) $(RUFF_FLAGS)

.PHONY: lint
lint: ## Run Ruff in lint-only mode for the Studio backend package
	$(RUFF) check $(PY_PATHS) $(RUFF_FLAGS)

.PHONY: fix
fix: ## Lint + safe auto-fixes for the Studio backend package
	$(RUFF) check $(PY_PATHS) --fix $(RUFF_FLAGS)

.PHONY: test
test: ## Run the Studio backend test suite
	$(PYTEST) tests

.PHONY: integration
integration: ## Run real PostgreSQL and Redis integration tests
	@test -n "$${RELAYNA_STUDIO_TEST_DATABASE_URL}" || (echo "Set RELAYNA_STUDIO_TEST_DATABASE_URL" >&2; exit 2)
	@test -n "$${RELAYNA_STUDIO_TEST_REDIS_URL}" || (echo "Set RELAYNA_STUDIO_TEST_REDIS_URL" >&2; exit 2)
	$(PYTEST) tests/test_postgres_persistence_integration.py

.PHONY: db-upgrade
db-upgrade: ## Upgrade the configured Studio PostgreSQL database to head
	@test -n "$${RELAYNA_STUDIO_DATABASE_URL}" || (echo "Set RELAYNA_STUDIO_DATABASE_URL" >&2; exit 2)
	$(ALEMBIC) upgrade head

.PHONY: db-downgrade
db-downgrade: ## Downgrade the configured Studio PostgreSQL database by one revision
	@test -n "$${RELAYNA_STUDIO_DATABASE_URL}" || (echo "Set RELAYNA_STUDIO_DATABASE_URL" >&2; exit 2)
	$(ALEMBIC) downgrade -1

.PHONY: db-current
db-current: ## Show the configured Studio PostgreSQL schema revision
	@test -n "$${RELAYNA_STUDIO_DATABASE_URL}" || (echo "Set RELAYNA_STUDIO_DATABASE_URL" >&2; exit 2)
	$(ALEMBIC) current

.PHONY: db-migration-cycle
db-migration-cycle: ## Destructively verify upgrade/downgrade/upgrade on a disposable database
	@test -n "$${RELAYNA_STUDIO_DATABASE_URL}" || (echo "Set RELAYNA_STUDIO_DATABASE_URL" >&2; exit 2)
	$(ALEMBIC) upgrade head
	$(ALEMBIC) downgrade base
	$(ALEMBIC) upgrade head

.PHONY: coverage
coverage: ## Run whole-package backend coverage gate
	$(UV) run --extra dev pytest --cov=relayna_studio --cov-report=term-missing tests

.PHONY: typecheck
typecheck: ## Static type analysis for the Studio backend package
	$(TY) check src/relayna_studio

.PHONY: build
build: ## Build the Studio backend wheel
	$(BUILD) --wheel

.PHONY: run
run: ## Run the Studio backend from source (requires PostgreSQL, Redis, and Entra settings)
	$(RUN) relayna-studio

.PHONY: docker-build
docker-build: ## Build the Studio backend Docker image
	docker build -f Dockerfile -t $(IMAGE_NAME) ../..

.PHONY: security
security: ## Audit Studio backend Python dependencies
	$(PIP_AUDIT) --skip-editable

.PHONY: clean
clean: ## Remove Studio backend caches & artefacts
	rm -rf .pytest_cache .ruff_cache dist build .coverage
