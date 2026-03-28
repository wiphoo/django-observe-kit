# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- **Trace Context Propagation**: Extract W3C Trace-Context headers from incoming requests for distributed tracing
- **Per-Sink PII Configuration**: Support different PII levels for logs, OTEL, Sentry, and audit sinks
- **DRF Integration Middleware**: Automatic detection of DRF ViewSet actions with span renaming (`drf.<ViewSet>.<action>`)
- **Framework Detection**: Automatic detection of Wagtail admin and Django admin requests with `framework` attribute
- **Error Handling**: Comprehensive error handling in all middleware to prevent observability failures from breaking requests
- **Trace ID in AuditLog**: Added `trace_id` field to AuditLog model for trace correlation
- **Request Body Sanitization Guards**: Automatic prevention of request/response body logging to protect PII
- **DB Tracking Performance Option**: Configurable flag to disable DB query tracking for high-traffic sites
- **Configuration Validation**: Comprehensive validation for `init_tracing()`, `init_sentry()`, and `configure_logging()` with clear error messages
- **Advanced Documentation**: Expanded README with advanced usage examples and customization guides
- **Type Hints**: Added comprehensive type hints for Django request/response objects using TYPE_CHECKING
- **Coverage Gate**: Default maintained test suite now enforces 90% package coverage

### Changed
- **TraceContextMiddleware**: Now extracts parent trace context from incoming requests instead of always creating new spans
- **RequestContext**: Added `framework` field to track request source (wagtail_admin, django_admin, etc.)
- **PII Configuration**: Refactored to support per-sink PII levels via `PiiConfig` class
- **Sentry Integration**: `init_sentry()` now uses per-sink PII config when `pii_level` not specified
- **Logging Configuration**: `configure_logging()` now accepts `pii_levels` dict for per-sink configuration
- **DRF Exception Handler**: Clarified documentation about ValidationError handling
- **Logging Filters**: Added automatic filtering of request/response body fields to prevent PII exposure
- **DB Tracking**: Made optional via `ENABLE_DB_TRACKING` configuration flag
- **Type Annotations**: Enhanced type hints across all middleware and core functions
- **Test Infrastructure**: Consolidated around maintained unit and integration suites

### Fixed
- Trace context propagation across microservices
- Missing `trace_id` in audit logs
- Incomplete DRF ViewSet action detection
- Missing Wagtail admin request tagging
- Lack of error handling in middleware
- Misleading DRF ValidationError documentation

### Documentation
- Updated README with per-sink PII configuration examples
- Added DRF middleware setup instructions
- Clarified ValidationError handling behavior
- Added migration instructions for AuditLog model
- Added comprehensive "Advanced Usage" section with examples for:
  - Custom span names and attributes
  - Using audit() helper
  - Disabling DB tracking
  - Request body sanitization
  - Configuration validation
  - Per-sink PII configuration
  - Custom exception handling
