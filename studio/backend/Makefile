# Studio backend Makefile.

UV              := uv
RUN             := $(UV) run
RUFF            := $(RUN) ruff
PYTEST          := $(RUN) pytest
TY              := $(RUN) ty
BUILD           := $(UV) build
LINE_LENGTH     := 120
RUFF_FLAGS      := --line-length $(LINE_LENGTH)
PY_PATHS        := src tests
IMAGE_NAME      := relayna-studio-backend

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

.PHONY: typecheck
typecheck: ## Static type analysis for the Studio backend package
	$(TY) check src/relayna_studio

.PHONY: build
build: ## Build the Studio backend wheel
	$(BUILD) --wheel

.PHONY: run
run: ## Run the Studio backend from source (requires RELAYNA_STUDIO_REDIS_URL)
	RELAYNA_STUDIO_REDIS_URL=$${RELAYNA_STUDIO_REDIS_URL:?Set RELAYNA_STUDIO_REDIS_URL} $(RUN) relayna-studio

.PHONY: docker-build
docker-build: ## Build the Studio backend Docker image
	docker build -f Dockerfile -t $(IMAGE_NAME) ../..

.PHONY: clean
clean: ## Remove Studio backend caches & artefacts
	rm -rf .pytest_cache .ruff_cache dist build .coverage
