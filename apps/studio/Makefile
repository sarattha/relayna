# Studio frontend Makefile.

NPM             := npm
IMAGE_NAME      := relayna-studio-frontend

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

.PHONY: sync
sync: ## Install frontend dependencies from package-lock.json
	$(NPM) ci

.PHONY: dev
dev: ## Run the Studio frontend dev server
	$(NPM) run dev

.PHONY: build
build: ## Build the Studio frontend bundle
	$(NPM) run build

.PHONY: test
test: ## Run the Studio frontend test suite
	$(NPM) run test

.PHONY: preview
preview: ## Preview the production frontend bundle locally
	$(NPM) run preview

.PHONY: docker-build
docker-build: ## Build the Studio frontend Docker image
	docker build -f Dockerfile -t $(IMAGE_NAME) ../..

.PHONY: clean
clean: ## Remove Studio frontend build artefacts
	rm -rf dist
