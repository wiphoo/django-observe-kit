# Django Observe Kit

[![CI](https://github.com/wiphoo/Django-Observe_Kit/actions/workflows/ci.yml/badge.svg)](https://github.com/wiphoo/Django-Observe_Kit/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Django 4.2+](https://img.shields.io/badge/django-4.2+-green.svg)](https://www.djangoproject.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

Drop-in observability for Django, DRF, and Wagtail. Observe Kit wires together request context, structured logging, OpenTelemetry tracing, Prometheus metrics, Sentry enrichment, audits, and optional Wagtail hooks, all with PII-aware defaults.

## Key Features
- **Context & tracing**: request-scoped contextvars, automated W3C trace context propagation, and an `X-Trace-Id` response header.
- **PII-aware logging**: JSON events (e.g., `request_complete`), configurable PII levels per sink, and automatic body/field sanitization.
- **DRF & Wagtail integration**: view-set action detection, span renaming, and audit hooks that carry tenant/trace metadata.
- **Metrics & health**: Prometheus counters, DB tracking controls, detailed `/healthz/detailed` reporting, and request duration instrumentation.
- **Audit & Sentry**: trace IDs in audit entries, enrichment for errors, and per-sink PII via `PiiConfig`.
- **Robust defaults**: graceful middleware error handling, optional DB tracking, and validation for OTEL/Sentry/logging configuration.

## Installation
```bash
pip install django-observe-kit
# with optional Wagtail hooks:
pip install "django-observe-kit[wagtail]"
```

For local development against a clone, install the project with its dev tools
(Ruff, mypy, pytest). The dev tools live in `[dependency-groups]`, not an extra:
```bash
uv sync --dev   # or: make init  (also installs pre-commit hooks)
```

## Developer quickstart
1. `make init` — install dependencies and set up pre-commit hooks.
2. `make check` — run Ruff, mypy, and formatting checks.
3. `make fix` — auto-format and auto-fix lint issues.
4. `make test-unit` — run the fast unit suite.
5. `make test-int` / `make integration-up` — start Docker services for integration tests (OTEL collector, Prometheus, HyperDX-style tools).

See [`docs/`](docs/README.md) for configuration, middleware, PII, and HyperDX guides.

## Testing
- `make test-unit`
- `make test-int` (after `make integration-up`)
- `make test-e2e`
- `make test-all`
- `pytest --cov=observe_kit --cov-fail-under=85` to enforce the coverage gate, with HTML reports in `htmlcov/`.

## Packaging & cleanup
- `make build`
- `make publish` (with safety checks)
- `make clean` to remove caches and build artifacts.

## Learn more
- [`docs/README.md`](docs/README.md) for configuration, middleware, PII, and HyperDX guides.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) for workflow guidance.
- [`CHANGELOG.md`](CHANGELOG.md) for release notes.
- [`SECURITY.md`](SECURITY.md) to report a vulnerability.
