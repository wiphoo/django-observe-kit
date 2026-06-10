# Repository Guidelines

## Project Structure & Module Organization

Core library code lives in `src/observe_kit/`, split by feature area such as `otel/`, `logging/`, `metrics/`, `sentry/`, `drf/`, `audit/`, and `wagtail_integration/`. Tests live under `tests/` with `unit/`, `integration/`, and `e2e/` suites; a few top-level `tests/test_*.py` files cover broader package behavior. Docker assets for integration testing are in `docker/compose/`. Runnable sample apps are in `examples/` (`django-core/`, `drf-observability/`, `otel-hyperdx/`, `grafana-metrics/`, `metrics-security/`, `sentry/`, `pii-sanitization/`, `audit-logs/`, `tenant-trace-security/`, `wagtail-observability/`). Treat `docs/internal/` as reference material, not the source of truth for commands.

## Build, Test, and Development Commands

This project uses `uv` and the `Makefile` as the standard entry points.

- `make init`: install all dependencies and set up pre-commit hooks.
- `make check`: run format check, Ruff lint, and Mypy type checks.
- `make fix`: auto-fix lint issues and apply Ruff formatting.
- `make test` or `make test-unit`: run all tests or only the fast unit suite.
- `make integration-up` then `make test-int`: start the Docker stack and run integration tests.
- `make ci`: run the same quality and coverage checks expected in CI.
- `make build`: build the package into `dist/`.

## Coding Style & Naming Conventions

Target Python 3.10+, 4-space indentation, and a 100-character line limit. Ruff handles formatting and import ordering; use double quotes and keep first-party imports under `observe_kit`. Mypy runs in strict mode, so add explicit type annotations for public functions and non-trivial internals. Follow existing file naming: snake_case modules, `Test*` classes where useful, and descriptive function names like `test_trace_propagation_sets_header`.

## Testing Guidelines

Pytest is the test runner, with coverage enforced at `--cov-fail-under=85`. Name tests `test_*.py` and keep them close to the behavior they cover. Use `@pytest.mark.integration` for Docker-backed tests and `@pytest.mark.e2e` for workflow tests. Prefer parametrization for matrix cases and add unit tests for any new logic before relying on integration coverage.

## Commit & Pull Request Guidelines

Recent history follows Conventional Commits, for example `fix: ...`, `feat: ...`, and `refactor(otel): ...`. Keep commit subjects imperative and scoped when helpful. Before opening a PR, run `make pr` or `make ci`, update `CHANGELOG.md` for user-facing changes, and include a clear description of behavior changes, test coverage, and any Docker or example-app impact.
