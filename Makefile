# Makefile for observe_kit
# Requires: uv (https://github.com/astral-sh/uv)

UV ?= $(shell command -v uv 2>/dev/null)
DOCKER_COMPOSE ?= docker compose
COMPOSE_FILE ?= docker/compose/integration.yml

# Ensure uv is available
ifeq ($(UV),)
$(error uv is required but not found. Install: https://github.com/astral-sh/uv)
endif

RUNNER = $(UV) run

.PHONY: help
.DEFAULT_GOAL := help

#=============================================================================
# Setup & Installation
#=============================================================================

.PHONY: init install

## Initialize project (install dependencies + pre-commit hooks)
init:
	@echo "🚀 Initializing project..."
	$(UV) sync --all-extras
	@if command -v pre-commit >/dev/null 2>&1; then \
		pre-commit install; \
	else \
		echo "⚠️  pre-commit not found, skipping hook installation"; \
	fi
	@echo "✅ Project initialized!"

## Install/update dependencies
install:
	$(UV) sync --all-extras

#=============================================================================
# Code Quality
#=============================================================================

.PHONY: fix check

## Fix linting + format (removes unused imports, sorts imports)
fix:
	$(RUNNER) ruff check --fix src tests
	$(RUNNER) ruff format src tests
	@echo "✅ Code fixed and formatted!"

## Run all checks (format-check, lint, typecheck)
check:
	$(RUNNER) ruff format --check src tests
	$(RUNNER) ruff check src tests
	$(RUNNER) mypy src
	@echo "✅ All quality checks passed!"

#=============================================================================
# Testing
#=============================================================================

.PHONY: test test-unit test-int test-e2e test-all test-cov test-cov-html test-watch

## Run all tests
test:
	$(RUNNER) pytest

## Run unit tests only
test-unit:
	$(RUNNER) pytest tests/unit -v

## Run integration tests (requires docker stack)
test-int:
	$(RUNNER) pytest tests/integration -v -m integration

## Run end-to-end tests
test-e2e:
	$(RUNNER) pytest tests/e2e -v -m e2e

## Run all test suites
test-all:
	$(RUNNER) pytest tests/unit tests/integration tests/e2e -v

## Run tests with coverage report
test-cov:
	$(RUNNER) pytest --cov=observe_kit --cov-report=term-missing

## Run tests and generate HTML coverage report
test-cov-html:
	$(RUNNER) pytest --cov=observe_kit --cov-report=html
	@echo "📊 Coverage report: htmlcov/index.html"

## Run tests in watch mode (requires pytest-watch)
test-watch:
	$(RUNNER) ptw --runner "pytest -v"

#=============================================================================
# Integration Testing
#=============================================================================

# =============================================================================
# Port Configuration (Source of Truth)
# =============================================================================
# All integration test service ports are defined here and exported for use in
# docker-compose and tests. Override via environment variables if needed.
#
# Note: HyperDX OTLP ports (4317, 4318) are internal only within Docker network
# and NOT exposed to host to avoid conflicts with OTEL Collector ports.

# OTEL Collector ports
OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_OTLP_HTTP_PORT ?= 4318
OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_OTLP_GRPC_PORT ?= 4317
OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_DEBUG_PORT ?= 8888

# Other service ports
OBSERVE_KIT_INTEGRATION_PROMETHEUS_PORT ?= 9090
OBSERVE_KIT_INTEGRATION_HYPERDX_PORT ?= 8080
OBSERVE_KIT_INTEGRATION_CLICKHOUSE_HTTP_PORT ?= 8123
OBSERVE_KIT_INTEGRATION_CLICKHOUSE_NATIVE_PORT ?= 9000

# Export all port variables for docker-compose
export OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_OTLP_HTTP_PORT \
       OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_OTLP_GRPC_PORT \
       OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_DEBUG_PORT \
       OBSERVE_KIT_INTEGRATION_PROMETHEUS_PORT \
       OBSERVE_KIT_INTEGRATION_HYPERDX_PORT \
       OBSERVE_KIT_INTEGRATION_CLICKHOUSE_HTTP_PORT \
       OBSERVE_KIT_INTEGRATION_CLICKHOUSE_NATIVE_PORT


.PHONY: integration-up integration-down integration-logs

## Start integration test stack
integration-up:
	@echo "🐳 Starting integration test stack..."
	@echo "📋 Port configuration:"
	@echo "   OTEL Collector HTTP: $(OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_OTLP_HTTP_PORT)"
	@echo "   OTEL Collector gRPC: $(OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_OTLP_GRPC_PORT)"
	@echo "   OTEL Collector Debug: $(OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_DEBUG_PORT)"
	@echo "   Prometheus: $(OBSERVE_KIT_INTEGRATION_PROMETHEUS_PORT)"
	@echo "   HyperDX UI: $(OBSERVE_KIT_INTEGRATION_HYPERDX_PORT)"
	@echo "   ClickHouse HTTP: $(OBSERVE_KIT_INTEGRATION_CLICKHOUSE_HTTP_PORT)"
	@echo "   ClickHouse Native: $(OBSERVE_KIT_INTEGRATION_CLICKHOUSE_NATIVE_PORT)"
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) up -d
	@echo "⏳ Waiting for services to be healthy..."
	@sleep 5
	@echo "✅ Integration stack is up!"

## Stop integration test stack
integration-down:
	@echo "🐳 Stopping integration test stack..."
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) down -v
	@echo "✅ Integration stack stopped!"

## View integration stack logs
integration-logs:
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) logs -f

#=============================================================================
# Build & Publish
#=============================================================================

.PHONY: build publish

## Build distribution package
build:
	@echo "📦 Building distribution package..."
	$(RUNNER) build
	@echo "✅ Build complete! Check dist/ directory."

## Publish package to PyPI (interactive)
publish:
	@echo "📤 Publishing package to PyPI..."
	@echo "⚠️  WARNING: Make sure you have:"
	@echo "   1. Updated version in pyproject.toml"
	@echo "   2. Created a git tag"
	@echo "   3. Built the package (make build)"
	@read -p "Continue? [y/N] " -n 1 -r; \
	echo; \
	if [[ $$REPLY =~ ^[Yy]$$ ]]; then \
		$(RUNNER) twine upload dist/*; \
	else \
		echo "❌ Publish cancelled."; \
	fi

#=============================================================================
# CI/CD
#=============================================================================

.PHONY: ci

## Run CI pipeline (format-check, lint, typecheck, test-cov)
ci: format-check lint typecheck test-cov
	@echo "✅ CI pipeline passed!"

#=============================================================================
# Cleanup
#=============================================================================

.PHONY: clean clean-all

## Remove build artifacts and caches
clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -name "*.pyc" -delete
	find . -type d -name ".pytest_cache" -prune -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -prune -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -prune -exec rm -rf {} +
	find . -type d -name "htmlcov" -prune -exec rm -rf {} +
	find . -name ".coverage" -delete
	find . -type d -name "dist" -prune -exec rm -rf {} +
	find . -type d -name "build" -prune -exec rm -rf {} +
	@echo "🧹 Cleanup complete!"

## Remove everything including virtual environment
clean-all: clean
	rm -rf .venv
	@echo "🧹 Deep cleanup complete!"

#=============================================================================
# Help
#=============================================================================

## Show this help message
help:
	@echo "Available targets:"
	@echo ""
	@grep -E '^##' $(MAKEFILE_LIST) | sed 's/^## /  /' | column -t -s ':'
	@echo ""
	@echo "Examples:"
	@echo "  make init          # First-time setup"
	@echo "  make test          # Run all tests"
	@echo "  make check         # Run all quality checks"
	@echo "  make ci            # Run full CI pipeline"
