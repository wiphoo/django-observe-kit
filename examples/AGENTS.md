# Repository Guidelines

## Project Structure & Module Organization

This repository is organized around runnable examples under `examples/`, one per `observe_kit` use case:

- `examples/otel-hyperdx/`: OTEL tracing, correlated logs, and local HyperDX stack
- `examples/django-core/`: plain Django request context, trace headers, logs, and metrics
- `examples/drf-observability/`: DRF ViewSet action detection and exception handling
- `examples/grafana-metrics/`: Prometheus-style metrics for Grafana
- `examples/metrics-security/`: `/metrics` token and staff access control
- `examples/sentry/`: Sentry error capture demo
- `examples/pii-sanitization/`: per-sink PII masking, hashing, and audit sanitization
- `examples/audit-logs/`: audit trail and trace correlation
- `examples/tenant-trace-security/`: tenant resolution, trusted trace context, and label caps
- `examples/wagtail-observability/`: Wagtail CMS workflow observability demo

Each example is self-contained and follows the same layout:

- `src/`: Django app and settings
- `tests/`: pytest suite
- `pyproject.toml`: dependencies and pytest config
- `uv.lock`: locked dependencies

The examples use Python 3.12, Django 4.x, `uv`, `pytest`, and `pytest-django`. API-focused examples use Django REST Framework.

Keep shared repo docs at the root and example-specific instructions inside each example’s `README.md`.

Local Claude-flow support files such as `.claude/`, `.claude-flow/`, and `.mcp.json` are tooling artifacts, not the main source of contributor guidance.

## Build, Test, and Development Commands

Run commands from the example you are working on.

- `cd examples/otel-hyperdx && uv sync`: install dependencies for that example
- `uv run python src/manage.py migrate`: apply local database migrations
- `uv run python src/manage.py runserver`: start the Django dev server
- `uv run pytest`: run that example’s test suite
- `uv lock`: refresh the lockfile after dependency changes

For the HyperDX example, also start the local stack with:

- `docker compose -f docker/compose/integration.yml --env-file docker/compose/.env up -d`

This repo does not use root-level `npm run build`, `npm test`, or `npm run lint` workflows.

## Coding Style & Naming Conventions

Use Python with 4-space indentation and follow PEP 8 style. Keep example code explicit and easy to read over abstract reuse. Use descriptive Django names such as `QuoteViewSet`, `DemoViewSet`, and `test_failure_endpoint_returns_500`. Prefer `snake_case` for functions, variables, modules, and test names.

Prefer editing existing example files over adding new top-level folders. Keep contributor docs at the root and keep runnable code inside the relevant example directory.

## Testing Guidelines

Tests use `pytest` with `pytest-django`. Place tests in each example’s `tests/` directory and name files `test_*.py`. Add focused behavior tests for each example’s main endpoint and observability outcome. Before opening a PR, run `uv run pytest` in every changed example.

## Commit & Pull Request Guidelines

Match the existing commit style: concise conventional messages like `feat(example): ...` and `refactor(examples): ...`. Keep commits scoped to a single change set. PRs should explain which example(s) changed, why the change was needed, how it was tested, and include screenshots only when updating the HyperDX flow or other UI-visible behavior.

Read the target file before editing it, and avoid mixing repo cleanup with feature changes unless the cleanup is required for correctness.

## Security & Configuration Tips

Do not commit local `.env` files, secrets, or generated virtualenv content. Commit `.env.example` files instead. Keep example service names and endpoints explicit so new contributors can verify behavior quickly.

`AGENTS.md` is the canonical contributor guide for this repository. Tool-specific files such as `CLAUDE.md`, `GEMINI.md`, `codex.md`, and `opencode.md` must be symbolic links to `AGENTS.md`, not copied instruction files.
