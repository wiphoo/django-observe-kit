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

.PHONY: lint format typecheck fix check

## Run ruff linting
lint:
	$(RUNNER) ruff check src tests

## Format code with ruff
format:
	$(RUNNER) ruff format src tests

## Check code formatting (without modifying)
format-check:
	$(RUNNER) ruff format --check src tests

## Run mypy type checking
typecheck:
	$(RUNNER) mypy src

## Fix linting + format (removes unused imports, sorts imports)
fix:
	$(RUNNER) ruff check --fix src tests
	$(RUNNER) ruff format src tests
	@echo "✅ Code fixed and formatted!"

## Run all checks (format-check, lint, typecheck)
check: format-check lint typecheck
	@echo "✅ All quality checks passed!"

#=============================================================================
# Testing
#=============================================================================

.PHONY: test test-unit test-int test-e2e test-all test-cov test-cov-html test-watch

## Run the default maintained test suite (unit tests with coverage)
test:
	$(RUNNER) pytest tests/unit -v --cov=observe_kit --cov-report=term-missing --cov-report=html --cov-fail-under=90

## Run unit tests only
test-unit:
	$(RUNNER) pytest tests/unit -v

## Run integration tests (requires docker stack)
test-int:
	$(RUNNER) pytest tests/integration -v -m integration

## Run end-to-end tests
test-e2e:
	@if find tests/e2e -type f -name 'test_*.py' | grep -q .; then \
		$(RUNNER) pytest tests/e2e -v -m e2e; \
	else \
		echo "No E2E tests found under tests/e2e; skipping."; \
	fi

## Run all test suites
test-all:
	$(RUNNER) pytest tests/unit tests/integration tests/e2e -v

## Run the default test suite with coverage report
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
OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_HEALTH_PORT ?= 13133

# Other service ports
OBSERVE_KIT_INTEGRATION_PROMETHEUS_PORT ?= 9090
OBSERVE_KIT_INTEGRATION_HYPERDX_PORT ?= 8080
# ClickHouse ports shifted to avoid conflicts (Forma pattern)
OBSERVE_KIT_INTEGRATION_CLICKHOUSE_HTTP_PORT ?= 28123
OBSERVE_KIT_INTEGRATION_CLICKHOUSE_NATIVE_PORT ?= 29000
OBSERVE_KIT_INTEGRATION_MONGODB_PORT ?= 27017

# Export all port variables for docker-compose
export OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_OTLP_HTTP_PORT \
       OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_OTLP_GRPC_PORT \
       OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_DEBUG_PORT \
       OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_HEALTH_PORT \
       OBSERVE_KIT_INTEGRATION_PROMETHEUS_PORT \
       OBSERVE_KIT_INTEGRATION_HYPERDX_PORT \
       OBSERVE_KIT_INTEGRATION_CLICKHOUSE_HTTP_PORT \
       OBSERVE_KIT_INTEGRATION_CLICKHOUSE_NATIVE_PORT \
       OBSERVE_KIT_INTEGRATION_MONGODB_PORT


.PHONY: integration-up integration-down integration-stop integration-clean integration-logs integration-status integration-wait integration-check-ports integration-hyperdx-login integration-hyperdx-open integration-prometheus-open integration-health

## Check for port conflicts before starting
integration-check-ports:
	@echo "🔍 Checking for port conflicts..."
	@conflicts=0; \
	ports="$(OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_OTLP_HTTP_PORT) $(OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_OTLP_GRPC_PORT) $(OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_DEBUG_PORT) $(OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_HEALTH_PORT) $(OBSERVE_KIT_INTEGRATION_PROMETHEUS_PORT) $(OBSERVE_KIT_INTEGRATION_HYPERDX_PORT) $(OBSERVE_KIT_INTEGRATION_CLICKHOUSE_HTTP_PORT) $(OBSERVE_KIT_INTEGRATION_CLICKHOUSE_NATIVE_PORT) $(OBSERVE_KIT_INTEGRATION_MONGODB_PORT)"; \
	for port in $$ports; do \
		if command -v ss >/dev/null 2>&1; then \
			if ss -tlnp 2>/dev/null | grep -q ":$$port "; then \
				echo "⚠️  Port $$port is already in use!"; \
				conflicts=1; \
			fi; \
		elif command -v lsof >/dev/null 2>&1; then \
			if lsof -Pi :$$port -sTCP:LISTEN -t >/dev/null 2>&1; then \
				echo "⚠️  Port $$port is already in use!"; \
				conflicts=1; \
			fi; \
		fi; \
	done; \
	if [ $$conflicts -eq 1 ]; then \
		echo "❌ Port conflicts detected. Please free the ports or update .env file."; \
		exit 1; \
	else \
		echo "✅ No port conflicts detected."; \
	fi

## Wait for services to be healthy
integration-wait:
	@echo "⏳ Waiting for services to be ready..."
	@timeout=120; \
	services_ready=0; \
	services_total=5; \
	echo ""; \
	for i in $$(seq 1 $$timeout); do \
		services_ready=0; \
		status_line=""; \
		\
		# Check OTEL Collector \
		if curl -sf http://localhost:$(OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_HEALTH_PORT)/ >/dev/null 2>&1; then \
			status_line="$$status_line ✅ OTEL Collector"; \
			services_ready=$$((services_ready + 1)); \
		else \
			status_line="$$status_line ⏳ OTEL Collector"; \
		fi; \
		\
		# Check Prometheus \
		if curl -sf http://localhost:$(OBSERVE_KIT_INTEGRATION_PROMETHEUS_PORT)/-/healthy >/dev/null 2>&1; then \
			status_line="$$status_line ✅ Prometheus"; \
			services_ready=$$((services_ready + 1)); \
		else \
			status_line="$$status_line ⏳ Prometheus"; \
		fi; \
		\
		# Check ClickHouse \
		if curl -sf http://localhost:$(OBSERVE_KIT_INTEGRATION_CLICKHOUSE_HTTP_PORT)/ping >/dev/null 2>&1; then \
			status_line="$$status_line ✅ ClickHouse"; \
			services_ready=$$((services_ready + 1)); \
		else \
			status_line="$$status_line ⏳ ClickHouse"; \
		fi; \
		\
		# Check MongoDB \
		if docker exec observe_kit-mongodb mongosh --eval "db.adminCommand('ping')" >/dev/null 2>&1; then \
			status_line="$$status_line ✅ MongoDB"; \
			services_ready=$$((services_ready + 1)); \
		else \
			status_line="$$status_line ⏳ MongoDB"; \
		fi; \
		\
		# Check HyperDX \
		if curl -sf http://localhost:$(OBSERVE_KIT_INTEGRATION_HYPERDX_PORT)/api/health >/dev/null 2>&1; then \
			status_line="$$status_line ✅ HyperDX"; \
			services_ready=$$((services_ready + 1)); \
		else \
			status_line="$$status_line ⏳ HyperDX"; \
		fi; \
		\
		printf "\r   [$$i/$$timeout] $$status_line ($$services_ready/$$services_total ready)"; \
		\
		if [ $$services_ready -eq $$services_total ]; then \
			echo ""; \
			echo "✅ All services are ready!"; \
			exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo ""; \
	echo "⚠️  Timeout waiting for services ($$services_ready/$$services_total ready)"; \
	echo ""; \
	echo "Service status:"; \
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) ps --format "table {{.Name}}\t{{.Status}}"

## Start integration test stack
integration-up: integration-check-ports
	@echo "🐳 Starting integration test stack..."
	@echo "📋 Port configuration:"
	@echo "   OTEL Collector HTTP: $(OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_OTLP_HTTP_PORT)"
	@echo "   OTEL Collector gRPC: $(OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_OTLP_GRPC_PORT)"
	@echo "   OTEL Collector Debug: $(OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_DEBUG_PORT)"
	@echo "   Prometheus: $(OBSERVE_KIT_INTEGRATION_PROMETHEUS_PORT)"
	@echo "   HyperDX UI: $(OBSERVE_KIT_INTEGRATION_HYPERDX_PORT)"
	@echo "   ClickHouse HTTP: $(OBSERVE_KIT_INTEGRATION_CLICKHOUSE_HTTP_PORT) (shifted from 8123)"
	@echo "   ClickHouse Native: $(OBSERVE_KIT_INTEGRATION_CLICKHOUSE_NATIVE_PORT) (shifted from 9000)"
	@echo "   MongoDB: $(OBSERVE_KIT_INTEGRATION_MONGODB_PORT)"
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) up -d
	@$(MAKE) integration-wait
	@echo "✅ Integration stack is up!"

## Stop integration test stack (preserve data)
integration-stop:
	@echo "🐳 Stopping integration stack (data preserved)..."
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) stop
	@echo "✅ Integration stack stopped!"

## Stop integration test stack and remove volumes
integration-down: integration-clean

## Stop integration test stack and remove all volumes
integration-clean:
	@echo "🐳 Stopping integration stack and removing all data..."
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) down -v
	@echo "✅ Integration stack stopped and cleaned!"

## View integration stack logs
integration-logs:
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) logs -f

## Check integration stack status
integration-status:
	@echo "📊 Integration stack status:"
	@$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) ps
	@echo ""
	@echo "🔗 Service URLs:"
	@echo "   OTEL Collector HTTP: http://localhost:$(OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_OTLP_HTTP_PORT)"
	@echo "   OTEL Collector gRPC: http://localhost:$(OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_OTLP_GRPC_PORT)"
	@echo "   Prometheus: http://localhost:$(OBSERVE_KIT_INTEGRATION_PROMETHEUS_PORT)"
	@echo "   HyperDX: http://localhost:$(OBSERVE_KIT_INTEGRATION_HYPERDX_PORT)"
	@echo "   ClickHouse HTTP: http://localhost:$(OBSERVE_KIT_INTEGRATION_CLICKHOUSE_HTTP_PORT)"
	@echo "   ClickHouse Native: localhost:$(OBSERVE_KIT_INTEGRATION_CLICKHOUSE_NATIVE_PORT)"
	@echo "   MongoDB: localhost:$(OBSERVE_KIT_INTEGRATION_MONGODB_PORT)"

## Show HyperDX login credentials
integration-hyperdx-login:
	@echo "🔐 HyperDX Login Credentials:"
	@echo "   URL: http://localhost:$(OBSERVE_KIT_INTEGRATION_HYPERDX_PORT)"
	@echo "   Email: $${HYPERDX_ADMIN_EMAIL:-admin@toffoli.co.th}"
	@echo "   Password: $${HYPERDX_ADMIN_PASSWORD:-admin123}"
	@echo ""
	@echo "💡 Tip: Bookmark this or add to your password manager!"

## Check health of integration services without waiting loop
integration-health:
	@echo "🏥 Checking integration service health..."
	@status=0; \
	if curl -sf http://localhost:$(OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_HEALTH_PORT)/ >/dev/null 2>&1; then \
		echo "✅ OTEL Collector"; \
	else \
		echo "❌ OTEL Collector"; \
		status=1; \
	fi; \
	if curl -sf http://localhost:$(OBSERVE_KIT_INTEGRATION_PROMETHEUS_PORT)/-/healthy >/dev/null 2>&1; then \
		echo "✅ Prometheus"; \
	else \
		echo "❌ Prometheus"; \
		status=1; \
	fi; \
	if curl -sf http://localhost:$(OBSERVE_KIT_INTEGRATION_CLICKHOUSE_HTTP_PORT)/ping >/dev/null 2>&1; then \
		echo "✅ ClickHouse"; \
	else \
		echo "❌ ClickHouse"; \
		status=1; \
	fi; \
	if docker exec observe_kit-mongodb mongosh --eval "db.adminCommand('ping')" >/dev/null 2>&1; then \
		echo "✅ MongoDB"; \
	else \
		echo "❌ MongoDB"; \
		status=1; \
	fi; \
	if curl -sf http://localhost:$(OBSERVE_KIT_INTEGRATION_HYPERDX_PORT)/api/health >/dev/null 2>&1; then \
		echo "✅ HyperDX"; \
	else \
		echo "❌ HyperDX"; \
		status=1; \
	fi; \
	exit $$status

## Open HyperDX UI in a browser
integration-hyperdx-open:
	@url="http://localhost:$(OBSERVE_KIT_INTEGRATION_HYPERDX_PORT)"; \
	if command -v xdg-open >/dev/null 2>&1; then \
		xdg-open "$$url"; \
	elif command -v open >/dev/null 2>&1; then \
		open "$$url"; \
	else \
		echo "❌ No supported browser opener found. Open $$url manually."; \
		exit 1; \
	fi

## Open Prometheus UI in a browser
integration-prometheus-open:
	@url="http://localhost:$(OBSERVE_KIT_INTEGRATION_PROMETHEUS_PORT)"; \
	if command -v xdg-open >/dev/null 2>&1; then \
		xdg-open "$$url"; \
	elif command -v open >/dev/null 2>&1; then \
		open "$$url"; \
	else \
		echo "❌ No supported browser opener found. Open $$url manually."; \
		exit 1; \
	fi

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
# Development Shortcuts
#=============================================================================

.PHONY: dev pr shell verify-install

## Quick dev loop: fix code + run unit tests
dev: fix test-unit
	@echo "✅ Dev cycle complete!"

## Pre-PR checks: full quality checks + tests
pr: check test-unit
	@echo "✅ Ready for PR!"

## Open Python shell with observe_kit loaded
shell:
	$(RUNNER) python -c "import observe_kit; import code; code.interact(local={'observe_kit': observe_kit})"

## Verify package installs correctly
verify-install:
	@echo "🔍 Verifying package installation..."
	$(UV) pip install -e . --quiet
	$(RUNNER) python -c "import observe_kit; print(f'✅ observe_kit {observe_kit.__version__ if hasattr(observe_kit, \"__version__\") else \"(no version)\"} imported successfully')"

#=============================================================================
# Examples
#=============================================================================

.PHONY: example-django example-drf example-wagtail

# Each example is a standalone uv project under examples/<name>/ with its own
# lockfile and manage.py at src/manage.py; run it via the example's own uv env.

## Run Django example
example-django:
	@echo "🚀 Starting Django example..."
	cd examples/django-core && uv sync && uv run python src/manage.py migrate
	cd examples/django-core && uv run python src/manage.py runserver

## Run DRF example
example-drf:
	@echo "🚀 Starting DRF example..."
	cd examples/drf-observability && uv sync && uv run python src/manage.py migrate
	cd examples/drf-observability && uv run python src/manage.py runserver

## Run Wagtail example
example-wagtail:
	@echo "🚀 Starting Wagtail example..."
	cd examples/wagtail-observability && uv sync && uv run python src/manage.py migrate
	cd examples/wagtail-observability && uv run python src/manage.py runserver

#=============================================================================
# CI/CD
#=============================================================================

.PHONY: ci

## Run CI pipeline (check + test with coverage)
ci: check test-cov
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
	@echo ""
	@echo "  observe_kit - Django Observability Toolkit"
	@echo "  ==========================================="
	@echo ""
	@echo "  Quick Start:"
	@echo "    make init          First-time setup (install deps + pre-commit)"
	@echo "    make dev           Quick dev loop (fix + test-unit)"
	@echo "    make pr            Pre-PR checks (check + test-unit)"
	@echo ""
	@echo "  Code Quality:"
	@echo "    make lint          Run ruff linting"
	@echo "    make format        Format code with ruff"
	@echo "    make typecheck     Run mypy type checking"
	@echo "    make fix           Auto-fix linting + format"
	@echo "    make check         Run all quality checks"
	@echo ""
	@echo "  Testing:"
	@echo "    make test          Run the default unit test suite"
	@echo "    make test-unit     Run unit tests only"
	@echo "    make test-int      Run integration tests (requires docker)"
	@echo "    make test-e2e      Run end-to-end tests"
	@echo "    make test-all      Run unit, integration, and end-to-end tests"
	@echo "    make test-cov      Run tests with coverage"
	@echo "    make test-cov-html Generate HTML coverage report"
	@echo ""
	@echo "  Integration Stack:"
	@echo "    make integration-up          Start Docker services"
	@echo "    make integration-down        Stop and remove services"
	@echo "    make integration-status      Show service status"
	@echo "    make integration-logs        View service logs"
	@echo "    make integration-health      Check service health"
	@echo "    make integration-hyperdx-open    Open HyperDX in browser"
	@echo "    make integration-prometheus-open  Open Prometheus in browser"
	@echo "    make integration-hyperdx-login    Show HyperDX credentials"
	@echo ""
	@echo "  Examples:"
	@echo "    make example-django    Run Django example"
	@echo "    make example-drf       Run DRF example"
	@echo "    make example-wagtail   Run Wagtail example"
	@echo ""
	@echo "  Build & Publish:"
	@echo "    make build         Build distribution package"
	@echo "    make publish       Publish to PyPI"
	@echo ""
	@echo "  Utilities:"
	@echo "    make shell         Python shell with observe_kit"
	@echo "    make clean         Remove build artifacts"
	@echo "    make clean-all     Remove everything (including .venv)"
	@echo ""
	@echo "  For more info: https://github.com/wiphoo/Django-Observe_Kit"
	@echo ""
