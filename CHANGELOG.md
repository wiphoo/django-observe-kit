# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- OTEL tracing with automatic W3C trace-context propagation plus the `X-Trace-Id` header.
- Per-sink PII configuration (`PiiConfig`) spanning logs, OTEL, Sentry, and audit sinks.
- DRF integration middleware that detects ViewSet actions and renames spans to `drf.<ViewSet>.<action>` while tagging frameworks.
- Structured logging, audit entries with trace IDs, request body sanitization, and optional DB-tracking controls.
- Health checks, validation helpers, and documentation that explain advanced usage and observability guardrails.

### Changed
- `configure_logging`, `init_tracing`, and `init_sentry` now validate inputs, honor per-sink PII, and document the new defaults.
- Middlewares gain graceful error handling, framework detection, and tighter span naming for DRF routes.
- Metrics, health checks, and Wagtail hooks now surface tenant/trace metadata consistently.

### Fixed
- Missing trace_id propagation in audit logs and logging handlers.
- DRF action detection gaps, Wagtail admin tagging, and middleware exceptions that previously halted requests.
- Misleading ValidationError documentation and coverage gaps around core middleware.

### Documentation
- README trimmed to the essentials with new pointers to the consolidated internal status summary.
- CONTRIBUTING now focuses on the minimal workflow, and CHANGELOG highlights only user-facing releases.
- Internal docs replaced by `docs/internal/status.md`, which lists the completed phases, testing flow, and next steps.
