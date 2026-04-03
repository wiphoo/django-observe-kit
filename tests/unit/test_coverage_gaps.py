"""Tests targeting coverage gaps in low-coverage modules."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# audit/__init__.py — __getattr__ lazy imports
# ---------------------------------------------------------------------------


class TestAuditInitLazyImports:
    def test_getattr_audit_log_returns_model(self) -> None:
        import observe_kit.audit as audit_module

        result = audit_module.__getattr__("AuditLog")
        # Should return the AuditLog class (or something truthy)
        assert result is not None
        assert result.__name__ == "AuditLog"

    def test_getattr_observe_audit_config_returns_app_config(self) -> None:
        import observe_kit.audit as audit_module

        result = audit_module.__getattr__("ObserveAuditConfig")
        assert result is not None
        assert result.__name__ == "ObserveAuditConfig"

    def test_getattr_unknown_name_raises_attribute_error(self) -> None:
        import observe_kit.audit as audit_module

        with pytest.raises(AttributeError, match="has no attribute 'NonExistent'"):
            audit_module.__getattr__("NonExistent")


# ---------------------------------------------------------------------------
# context.py — LookupError path in get_request_context
# ---------------------------------------------------------------------------


class TestGetRequestContextLookupError:
    def test_returns_provided_default_when_context_var_unset(self) -> None:
        """When ContextVar has no value (LookupError), get_request_context returns the default."""
        import contextvars

        from observe_kit.context import RequestContext, get_request_context

        results: list[Any] = []

        def _run_fresh() -> None:
            # In a fresh copy of the context, _request_context has no value → LookupError
            my_default = RequestContext(method="PUT")
            result = get_request_context(default=my_default)
            results.append(result)

        # Run in a context that never had _request_context set
        fresh_ctx = contextvars.Context()
        fresh_ctx.run(_run_fresh)

        assert results[0].method == "PUT"

    def test_creates_empty_default_when_none_provided_and_context_var_unset(self) -> None:
        """When ContextVar has no value, get_request_context() creates a fresh RequestContext."""
        import contextvars

        from observe_kit.context import RequestContext, get_request_context

        results: list[Any] = []

        def _run_fresh() -> None:
            result = get_request_context()
            results.append(result)

        fresh_ctx = contextvars.Context()
        fresh_ctx.run(_run_fresh)

        assert isinstance(results[0], RequestContext)
        assert results[0].method is None


# ---------------------------------------------------------------------------
# logging/config.py — validation errors + add_fields formatter
# ---------------------------------------------------------------------------


class TestLoggingConfigValidation:
    def test_invalid_log_level_raises_configuration_error(self) -> None:
        from observe_kit.logging.config import ConfigurationError, configure_logging

        with pytest.raises(ConfigurationError, match="level must be one of"):
            configure_logging(level="VERBOSE")

    def test_log_level_must_be_string(self) -> None:
        from observe_kit.logging.config import ConfigurationError, configure_logging

        with pytest.raises(ConfigurationError, match="level must be a string"):
            configure_logging(level=42)  # type: ignore[arg-type]

    def test_invalid_pii_level_raises_configuration_error(self) -> None:
        from observe_kit.logging.config import ConfigurationError, configure_logging

        with pytest.raises(ConfigurationError, match="pii_level must be one of"):
            configure_logging(pii_level="HIGH")

    def test_pii_level_must_be_string(self) -> None:
        from observe_kit.logging.config import ConfigurationError, configure_logging

        with pytest.raises(ConfigurationError, match="pii_level must be a string"):
            configure_logging(pii_level=1)  # type: ignore[arg-type]

    def test_invalid_pii_levels_sink_raises_configuration_error(self) -> None:
        from observe_kit.logging.config import ConfigurationError, configure_logging

        with pytest.raises(ConfigurationError, match="Invalid sink"):
            configure_logging(pii_levels={"unknown_sink": "BASIC"})

    def test_invalid_pii_levels_value_raises_configuration_error(self) -> None:
        from observe_kit.logging.config import ConfigurationError, configure_logging

        with pytest.raises(ConfigurationError, match="Invalid PII level"):
            configure_logging(pii_levels={"logs": "MEDIUM"})

    def test_pii_levels_must_be_dict(self) -> None:
        from observe_kit.logging.config import ConfigurationError, configure_logging

        with pytest.raises(ConfigurationError, match="pii_levels must be a dictionary"):
            configure_logging(pii_levels="BASIC")  # type: ignore[arg-type]


class TestRequestFormatterAddFields:
    def test_add_fields_sets_level_and_logger(self) -> None:
        from observe_kit.logging.config import RequestFormatter

        formatter = RequestFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        log_record: dict[str, Any] = {}
        formatter.add_fields(log_record, record, {})
        assert log_record["level"] == "INFO"
        assert log_record["logger"] == "test.logger"

    def test_add_fields_does_not_override_existing_level(self) -> None:
        from observe_kit.logging.config import RequestFormatter

        formatter = RequestFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.WARNING,
            pathname="",
            lineno=0,
            msg="warn",
            args=(),
            exc_info=None,
        )
        log_record: dict[str, Any] = {"level": "ALREADY_SET"}
        formatter.add_fields(log_record, record, {})
        assert log_record["level"] == "ALREADY_SET"


# ---------------------------------------------------------------------------
# sentry/middleware.py — old configure_scope API path (lines 35-43)
# ---------------------------------------------------------------------------


class TestSentryContextMiddlewareOldApi:
    def _make_sentry_middleware(self) -> Any:
        from observe_kit.sentry.middleware import SentryContextMiddleware

        return SentryContextMiddleware(get_response=MagicMock())

    def test_falls_back_to_configure_scope_when_get_isolation_scope_missing(self) -> None:
        """When sentry_sdk lacks get_isolation_scope, configure_scope fallback is used."""
        from observe_kit.context import RequestContext, set_request_context

        ctx = RequestContext(trace_id="abc123", tenant_id="tenant-1", method="GET", path="/old-api")
        set_request_context(ctx)

        scope_mock = MagicMock()
        mock_sentry = MagicMock(spec=["configure_scope"])
        mock_sentry.configure_scope.return_value.__enter__ = lambda s: scope_mock
        mock_sentry.configure_scope.return_value.__exit__ = MagicMock(return_value=False)

        middleware = self._make_sentry_middleware()
        with patch.dict("sys.modules", {"sentry_sdk": mock_sentry}):
            with patch("importlib.util.find_spec", return_value=MagicMock()):
                middleware.process_request(MagicMock())

        scope_mock.set_tag.assert_any_call("otel.trace_id", "abc123")
        scope_mock.set_tag.assert_any_call("tenant_id", "tenant-1")

    def test_uses_get_isolation_scope_when_available(self) -> None:
        """When sentry_sdk has get_isolation_scope, the new API path is used."""
        from observe_kit.context import RequestContext, set_request_context

        ctx = RequestContext(trace_id="def456", tenant_id="t2", method="POST", path="/new-api")
        set_request_context(ctx)

        scope_mock = MagicMock()
        mock_sentry = MagicMock()
        mock_sentry.get_isolation_scope.return_value = scope_mock

        middleware = self._make_sentry_middleware()
        with patch.dict("sys.modules", {"sentry_sdk": mock_sentry}):
            with patch("importlib.util.find_spec", return_value=MagicMock()):
                middleware.process_request(MagicMock())

        scope_mock.set_tag.assert_any_call("otel.trace_id", "def456")

    def test_handles_exception_gracefully(self) -> None:
        mock_sentry = MagicMock()
        mock_sentry.get_isolation_scope.side_effect = RuntimeError("sentry down")

        middleware = self._make_sentry_middleware()
        with patch.dict("sys.modules", {"sentry_sdk": mock_sentry}):
            with patch("importlib.util.find_spec", return_value=MagicMock()):
                result = middleware.process_request(MagicMock())
        assert result is None

    def test_skips_when_sentry_sdk_not_installed(self) -> None:
        middleware = self._make_sentry_middleware()
        with patch("importlib.util.find_spec", return_value=None):
            result = middleware.process_request(MagicMock())
        assert result is None


# ---------------------------------------------------------------------------
# otel/middleware.py — process_exception, zero parent_span_id, fallback span.end()
# ---------------------------------------------------------------------------


class TestTraceContextMiddlewareProcessException:
    def _make_middleware(self) -> Any:
        from observe_kit.otel.middleware import TraceContextMiddleware

        return TraceContextMiddleware(get_response=MagicMock())

    def test_process_exception_records_exception_on_span(self) -> None:
        middleware = self._make_middleware()
        span_mock = MagicMock()
        request = MagicMock()
        request._observe_kit_span = span_mock

        exc = ValueError("test error")
        middleware.process_exception(request, exc)

        span_mock.record_exception.assert_called_once_with(exc)
        span_mock.set_status.assert_called_once()

    def test_process_exception_no_span_is_noop(self) -> None:
        middleware = self._make_middleware()
        request = MagicMock(spec=[])  # no _observe_kit_span attribute

        # Should not raise
        middleware.process_exception(request, RuntimeError("boom"))


class TestTraceContextMiddlewareZeroParentSpanId:
    def _make_middleware(self) -> Any:
        from observe_kit.otel.middleware import TraceContextMiddleware

        return TraceContextMiddleware(get_response=MagicMock())

    def test_zero_parent_span_id_creates_synthetic_span(self) -> None:
        middleware = self._make_middleware()
        # traceparent with zero parent_span_id
        traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01"
        fallback = MagicMock()

        result = middleware._extract_trace_context_with_zero_parent(traceparent, fallback)
        # Should return a new context (not the fallback) because span_id is zero
        assert result is not fallback

    def test_nonzero_parent_span_id_returns_fallback(self) -> None:
        middleware = self._make_middleware()
        traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        fallback = MagicMock()

        result = middleware._extract_trace_context_with_zero_parent(traceparent, fallback)
        assert result is fallback

    def test_malformed_traceparent_returns_fallback(self) -> None:
        middleware = self._make_middleware()
        fallback = MagicMock()

        result = middleware._extract_trace_context_with_zero_parent("bad-header", fallback)
        assert result is fallback

    def test_traceparent_with_invalid_hex_returns_fallback(self) -> None:
        middleware = self._make_middleware()
        fallback = MagicMock()

        result = middleware._extract_trace_context_with_zero_parent(
            "00-ZZZZZZZZZZZZZZZZ-0000000000000000-01", fallback
        )
        assert result is fallback


class TestTraceContextMiddlewareFallbackSpanEnd:
    def test_fallback_span_end_called_when_context_manager_missing(self) -> None:
        """When _observe_kit_span_context_manager is absent, span.end() is called."""
        from observe_kit.otel.middleware import TraceContextMiddleware

        middleware = TraceContextMiddleware(get_response=MagicMock())
        span_mock = MagicMock()
        span_mock.get_span_context.return_value = MagicMock(trace_id=1234567890)

        request = MagicMock()
        request._observe_kit_span = span_mock
        # No span_context_manager set
        del request._observe_kit_span_context_manager

        response = MagicMock()
        response.status_code = 200

        with patch("observe_kit.otel.middleware.get_request_context") as mock_ctx:
            mock_ctx.return_value = MagicMock(route="/test")
            result = middleware.process_response(request, response)

        span_mock.end.assert_called_once()
        assert result is response


# ---------------------------------------------------------------------------
# settings.py — exception path in get_observe_kit_settings
# ---------------------------------------------------------------------------


class TestSettingsExceptionPath:
    def test_settings_returns_defaults_when_django_import_raises(self) -> None:
        import sys

        from observe_kit.settings import get_observe_kit_settings

        original = sys.modules.get("django.conf")
        try:
            sys.modules["django.conf"] = None  # type: ignore[assignment]
            result = get_observe_kit_settings()
            # Should return defaults without raising
            assert result.configured is False
            assert result.log_level == "INFO"
        finally:
            if original is None:
                sys.modules.pop("django.conf", None)
            else:
                sys.modules["django.conf"] = original

    def test_otel_sample_rate_parse_error_defaults_to_none(self) -> None:
        from django.conf import settings as django_settings

        from observe_kit.settings import get_observe_kit_settings

        original = getattr(django_settings, "OBSERVE_KIT", None)
        try:
            django_settings.OBSERVE_KIT = {"OTEL_SAMPLE_RATE": "not-a-float"}  # type: ignore[attr-defined]
            result = get_observe_kit_settings()
            assert result.otel_sample_rate is None
        finally:
            if original is None and hasattr(django_settings, "OBSERVE_KIT"):
                delattr(django_settings, "OBSERVE_KIT")
            elif original is not None:
                django_settings.OBSERVE_KIT = original  # type: ignore[attr-defined]

    def test_sentry_traces_sample_rate_parse_error_defaults_to_zero(self) -> None:
        from django.conf import settings as django_settings

        from observe_kit.settings import get_observe_kit_settings

        original = getattr(django_settings, "OBSERVE_KIT", None)
        try:
            django_settings.OBSERVE_KIT = {"SENTRY_TRACES_SAMPLE_RATE": "bad-value"}  # type: ignore[attr-defined]
            result = get_observe_kit_settings()
            assert result.sentry_traces_sample_rate == 0.0
        finally:
            if original is None and hasattr(django_settings, "OBSERVE_KIT"):
                delattr(django_settings, "OBSERVE_KIT")
            elif original is not None:
                django_settings.OBSERVE_KIT = original  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# wagtail_integration/wagtail_hooks.py — _with_span wrapper execution
# ---------------------------------------------------------------------------


class TestWagtailHooksWithSpan:
    def test_with_span_wrapper_creates_span_and_calls_func(self) -> None:
        """_with_span wraps a function so it runs inside a tracer span."""
        # We need wagtail available to import the hooks module
        wagtail_spec = pytest.importorskip("importlib.util").find_spec("wagtail")
        if wagtail_spec is None:
            pytest.skip("wagtail not installed")

        from observe_kit.wagtail_integration import wagtail_hooks

        inner = MagicMock(return_value="result")
        span_mock = MagicMock()
        tracer_mock = MagicMock()
        tracer_mock.start_as_current_span.return_value.__enter__ = lambda s: span_mock
        tracer_mock.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(wagtail_hooks, "tracer", tracer_mock):
            wrapped = wagtail_hooks._with_span("test_op", inner)
            page = MagicMock(id=42)
            request = MagicMock()
            result = wrapped(page, request)

        assert result == "result"
        inner.assert_called_once_with(page, request, span=span_mock)
        tracer_mock.start_as_current_span.assert_called_once_with("test_op")

    def test_audit_publish_page_increments_counter_and_logs(self) -> None:
        wagtail_spec = pytest.importorskip("importlib.util").find_spec("wagtail")
        if wagtail_spec is None:
            pytest.skip("wagtail not installed")

        from observe_kit.context import RequestContext, set_request_context
        from observe_kit.wagtail_integration import wagtail_hooks

        ctx = RequestContext(tenant_id="tenant-42")
        set_request_context(ctx)

        page = MagicMock(id=99)
        request = MagicMock()

        with patch.object(wagtail_hooks, "WAGTAIL_PUBLISHED") as mock_counter:
            with patch("observe_kit.wagtail_integration.wagtail_hooks.audit") as mock_audit:
                wagtail_hooks.audit_publish_page(request, page)

        mock_counter.labels.assert_called_once_with("tenant-42")
        mock_counter.labels.return_value.inc.assert_called_once()
        mock_audit.assert_called_once()


# ---------------------------------------------------------------------------
# audit/admin.py — has_change_permission / has_delete_permission
# ---------------------------------------------------------------------------


class TestAuditAdminPermissions:
    def test_has_change_permission_returns_false(self) -> None:
        from observe_kit.audit.admin import AuditLogAdmin

        admin_instance = AuditLogAdmin.__new__(AuditLogAdmin)
        assert admin_instance.has_change_permission(MagicMock()) is False
        assert admin_instance.has_change_permission(MagicMock(), obj=MagicMock()) is False

    def test_has_delete_permission_returns_false(self) -> None:
        from observe_kit.audit.admin import AuditLogAdmin

        admin_instance = AuditLogAdmin.__new__(AuditLogAdmin)
        assert admin_instance.has_delete_permission(MagicMock()) is False
        assert admin_instance.has_delete_permission(MagicMock(), obj=MagicMock()) is False
