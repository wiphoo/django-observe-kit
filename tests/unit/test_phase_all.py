"""Tests for Phase 1/2/3 hardening: immutable audit log, extensible PII,
hash salt, DB wrapper leak fix, trusted proxy IP, span sampling,
audit before/after, and body sanitization."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

from tests.unit.conftest import make_observe_kit_settings as _make_settings

# ---------------------------------------------------------------------------
# Phase 1 – AuditLog immutability
# ---------------------------------------------------------------------------


class TestAuditLogImmutability:
    """AuditLog must never be mutated or deleted after creation."""

    def test_save_raises_on_update(self) -> None:
        from observe_kit.audit.models import AuditLog

        entry = AuditLog()
        entry.pk = 99  # simulate an already-persisted record
        entry.action = "tampered"
        with pytest.raises(PermissionError, match="immutable"):
            entry.save()

    def test_delete_raises(self) -> None:
        from observe_kit.audit.models import AuditLog

        entry = AuditLog()
        entry.pk = 99
        with pytest.raises(PermissionError, match="immutable"):
            entry.delete()

    def test_first_save_calls_super(self) -> None:
        from observe_kit.audit.models import AuditLog

        entry = AuditLog()
        assert entry.pk is None
        # When pk is None, save() delegates to super().save() — no PermissionError
        with patch("django.db.models.Model.save") as mock_super_save:
            entry.save()
            mock_super_save.assert_called_once()


# ---------------------------------------------------------------------------
# Phase 1 – Extensible PII field lists
# ---------------------------------------------------------------------------


class TestExtensiblePiiLists:
    def test_sanitize_headers_extra_drop(self) -> None:
        from observe_kit.pii_rules import PiiLevel, sanitize_headers

        result = sanitize_headers(
            {"x-custom-secret": "abc", "accept": "json"},
            PiiLevel.BASIC,
            extra_drop=frozenset({"x-custom-secret"}),
        )
        assert "x-custom-secret" not in result
        assert "accept" in result

    def test_sanitize_headers_extra_mask(self) -> None:
        from observe_kit.pii_rules import PiiLevel, sanitize_headers

        result = sanitize_headers(
            {"national-id": "123456789"}, PiiLevel.BASIC, extra_mask=frozenset({"national-id"})
        )
        assert result["national-id"] == "12***"

    def test_sanitize_query_params_extra_drop(self) -> None:
        from observe_kit.pii_rules import PiiLevel, sanitize_query_params

        result = sanitize_query_params(
            {"token": "abc", "page": "1"}, PiiLevel.BASIC, extra_drop=frozenset({"token"})
        )
        assert "token" not in result
        assert "page" in result

    def test_sanitize_headers_extra_hash_sensitive(self) -> None:
        from observe_kit.pii_rules import PiiLevel, sanitize_headers

        result = sanitize_headers(
            {"device-id": "ABC123"}, PiiLevel.SENSITIVE, extra_hash=frozenset({"device-id"})
        )
        # hashed value is a 64-char hex string
        assert len(result["device-id"]) == 64


# ---------------------------------------------------------------------------
# Phase 1 – PII hash salt
# ---------------------------------------------------------------------------


class TestPiiHashSalt:
    def test_same_value_different_salt_differs(self) -> None:
        from observe_kit.pii_rules import _hash_value

        h1 = _hash_value("192.168.1.1", salt="salt-a")
        h2 = _hash_value("192.168.1.1", salt="salt-b")
        assert h1 != h2

    def test_no_salt_is_deterministic(self) -> None:
        from observe_kit.pii_rules import _hash_value

        assert _hash_value("192.168.1.1") == _hash_value("192.168.1.1")

    def test_sanitize_headers_uses_salt(self) -> None:
        from observe_kit.pii_rules import PiiLevel, sanitize_headers

        result_a = sanitize_headers(
            {"user-agent": "Mozilla/5.0"}, PiiLevel.SENSITIVE, hash_salt="salt-a"
        )
        result_b = sanitize_headers(
            {"user-agent": "Mozilla/5.0"}, PiiLevel.SENSITIVE, hash_salt="salt-b"
        )
        assert result_a["user-agent"] != result_b["user-agent"]

    def test_empty_salt_matches_no_salt(self) -> None:
        from observe_kit.pii_rules import _hash_value

        assert _hash_value("1.2.3.4", salt="") == _hash_value("1.2.3.4")


# ---------------------------------------------------------------------------
# Phase 1 – Settings new fields
# ---------------------------------------------------------------------------


class TestNewSettings:
    def test_defaults(self, observe_kit_settings: Any) -> None:
        with observe_kit_settings({}):
            from observe_kit.settings import get_observe_kit_settings

            cfg = get_observe_kit_settings()
        assert cfg.pii_hash_salt == ""
        assert cfg.extra_drop_headers == frozenset()
        assert cfg.extra_mask_fields == frozenset()
        assert cfg.extra_hash_fields == frozenset()
        assert cfg.trusted_proxies == []
        assert cfg.otel_sample_rate is None

    def test_extra_pii_fields_parsed(self, observe_kit_settings: Any) -> None:
        with observe_kit_settings(
            {
                "EXTRA_DROP_HEADERS": ["x-secret"],
                "EXTRA_MASK_FIELDS": ["national-id"],
                "EXTRA_HASH_FIELDS": ["device-id"],
                "PII_HASH_SALT": "my-salt",
            }
        ):
            from observe_kit.settings import get_observe_kit_settings

            cfg = get_observe_kit_settings()
        assert "x-secret" in cfg.extra_drop_headers
        assert "national-id" in cfg.extra_mask_fields
        assert "device-id" in cfg.extra_hash_fields
        assert cfg.pii_hash_salt == "my-salt"

    def test_trusted_proxies_parsed(self, observe_kit_settings: Any) -> None:
        with observe_kit_settings({"TRUSTED_PROXIES": ["10.0.0.1", "10.0.0.2"]}):
            from observe_kit.settings import get_observe_kit_settings

            cfg = get_observe_kit_settings()
        assert cfg.trusted_proxies == ["10.0.0.1", "10.0.0.2"]

    def test_otel_sample_rate_clamped(self, observe_kit_settings: Any) -> None:
        with observe_kit_settings({"OTEL_SAMPLE_RATE": 1.5}):
            from observe_kit.settings import get_observe_kit_settings

            cfg = get_observe_kit_settings()
        assert cfg.otel_sample_rate == 1.0

    def test_otel_sample_rate_valid(self, observe_kit_settings: Any) -> None:
        with observe_kit_settings({"OTEL_SAMPLE_RATE": 0.25}):
            from observe_kit.settings import get_observe_kit_settings

            cfg = get_observe_kit_settings()
        assert cfg.otel_sample_rate == 0.25

    def test_otel_sample_rate_invalid_string_gives_none(self, observe_kit_settings: Any) -> None:
        with observe_kit_settings({"OTEL_SAMPLE_RATE": "bad"}):
            from observe_kit.settings import get_observe_kit_settings

            cfg = get_observe_kit_settings()
        assert cfg.otel_sample_rate is None


# ---------------------------------------------------------------------------
# Phase 1 – AppConfig error handling
# ---------------------------------------------------------------------------


class TestAppConfigReadyErrorHandling:
    def _run_ready_with_failure(self, which: str) -> None:
        cfg = _make_settings(
            service_name="svc" if which == "otel" else None,
            sentry_dsn="https://key@sentry.io/1" if which == "sentry" else None,
        )
        with patch("observe_kit.settings.get_observe_kit_settings", return_value=cfg):
            with patch("observe_kit.logging.configure_logging") as mock_log:
                mock_log.side_effect = RuntimeError("log boom") if which == "log" else None
                with patch("observe_kit.otel.init_tracing") as mock_trace:
                    mock_trace.side_effect = RuntimeError("trace boom") if which == "otel" else None
                    with patch("observe_kit.sentry.init_sentry") as mock_sentry:
                        mock_sentry.side_effect = (
                            RuntimeError("sentry boom") if which == "sentry" else None
                        )
                        from observe_kit.apps import ObserveKitConfig

                        app = ObserveKitConfig.create("observe_kit")
                        app.ready()  # must not raise

    def test_logging_failure_does_not_crash_ready(self) -> None:
        self._run_ready_with_failure("log")

    def test_otel_failure_does_not_crash_ready(self) -> None:
        self._run_ready_with_failure("otel")

    def test_sentry_failure_does_not_crash_ready(self) -> None:
        self._run_ready_with_failure("sentry")


# ---------------------------------------------------------------------------
# Phase 2 – Trusted proxy IP resolution
# ---------------------------------------------------------------------------


class TestTrustedProxyIpResolution:
    def _make_request(self, remote_addr: str, xff: str | None = None) -> MagicMock:
        req = MagicMock()
        meta: dict[str, str] = {"REMOTE_ADDR": remote_addr}
        if xff:
            meta["HTTP_X_FORWARDED_FOR"] = xff
        req.META = meta
        return req

    def test_no_trusted_proxies_uses_remote_addr(self) -> None:
        from observe_kit.context_middleware import _resolve_remote_addr

        req = self._make_request("1.2.3.4", xff="10.0.0.1")
        assert _resolve_remote_addr(req, []) == "1.2.3.4"

    def test_trusted_proxy_uses_xff_leftmost(self) -> None:
        from observe_kit.context_middleware import _resolve_remote_addr

        req = self._make_request("10.0.0.1", xff="203.0.113.5, 10.0.0.1")
        assert _resolve_remote_addr(req, ["10.0.0.1"]) == "203.0.113.5"

    def test_wildcard_trusted_proxy_uses_xff(self) -> None:
        from observe_kit.context_middleware import _resolve_remote_addr

        req = self._make_request("192.168.1.1", xff="5.6.7.8")
        assert _resolve_remote_addr(req, ["*"]) == "5.6.7.8"

    def test_untrusted_remote_addr_ignores_xff(self) -> None:
        from observe_kit.context_middleware import _resolve_remote_addr

        req = self._make_request("9.9.9.9", xff="203.0.113.5")
        assert _resolve_remote_addr(req, ["10.0.0.1"]) == "9.9.9.9"

    def test_no_xff_header_falls_back_to_remote_addr(self) -> None:
        from observe_kit.context_middleware import _resolve_remote_addr

        req = self._make_request("10.0.0.1")
        assert _resolve_remote_addr(req, ["10.0.0.1"]) == "10.0.0.1"


# ---------------------------------------------------------------------------
# Phase 2 – DB wrapper leak fix (process_exception removes wrappers)
# ---------------------------------------------------------------------------


class TestDbWrapperLeakFix:
    def test_process_exception_removes_wrappers(self) -> None:
        from observe_kit.context_middleware import RequestContextMiddleware

        mw = RequestContextMiddleware.__new__(RequestContextMiddleware)
        mw._extra_drop = frozenset()
        mw._extra_mask = frozenset()
        mw._extra_hash = frozenset()
        mw._hash_salt = ""
        mw._trusted_proxies = []
        mw.pii_level = None

        remover_called: list[bool] = []
        req = MagicMock()
        req._observe_kit_remove_wrappers = lambda: remover_called.append(True)

        mw.process_exception(req, Exception("boom"))

        assert remover_called == [True]
        assert req._observe_kit_remove_wrappers is None

    def test_process_exception_handles_missing_wrapper(self) -> None:
        from observe_kit.context_middleware import RequestContextMiddleware

        mw = RequestContextMiddleware.__new__(RequestContextMiddleware)
        req = MagicMock(spec=[])  # no _observe_kit_remove_wrappers attribute
        mw.process_exception(req, Exception("boom"))  # must not raise

    def test_process_exception_handles_remover_raising(self) -> None:
        from observe_kit.context_middleware import RequestContextMiddleware

        mw = RequestContextMiddleware.__new__(RequestContextMiddleware)
        req = MagicMock()
        req._observe_kit_remove_wrappers = MagicMock(side_effect=RuntimeError("fail"))
        mw.process_exception(req, Exception("view error"))  # must not raise
        assert req._observe_kit_remove_wrappers is None


# ---------------------------------------------------------------------------
# Phase 2 – OTel span sampling
# ---------------------------------------------------------------------------


class TestOtelSpanSampling:
    def test_init_tracing_with_sample_rate(self) -> None:
        from opentelemetry.sdk.trace.sampling import ParentBased

        from observe_kit.otel.config import init_tracing

        with (
            patch("observe_kit.otel.config.trace.set_tracer_provider") as mock_set,
            patch("observe_kit.otel.config._init_otel_log_export"),
            patch("observe_kit.otel.config._TRACING_INITIALIZED", False),
        ):
            init_tracing(service_name="svc-sample", sample_rate=0.5)
            provider_arg = mock_set.call_args[0][0]
            assert isinstance(provider_arg.sampler, ParentBased)

    def test_init_tracing_without_sample_rate_uses_always_on(self) -> None:
        from opentelemetry.sdk.trace.sampling import ALWAYS_ON

        from observe_kit.otel.config import init_tracing

        with (
            patch("observe_kit.otel.config.trace.set_tracer_provider") as mock_set,
            patch("observe_kit.otel.config._init_otel_log_export"),
            patch("observe_kit.otel.config._TRACING_INITIALIZED", False),
        ):
            init_tracing("svc-no-sample", sample_rate=None)
            provider_arg = mock_set.call_args[0][0]
            assert provider_arg.sampler is ALWAYS_ON

    def test_sample_rate_zero_uses_parent_based(self) -> None:
        from opentelemetry.sdk.trace.sampling import ParentBased

        from observe_kit.otel.config import init_tracing

        with (
            patch("observe_kit.otel.config.trace.set_tracer_provider") as mock_set,
            patch("observe_kit.otel.config._init_otel_log_export"),
            patch("observe_kit.otel.config._TRACING_INITIALIZED", False),
        ):
            init_tracing("svc-zero-sample", sample_rate=0.0)
            provider_arg = mock_set.call_args[0][0]
            assert isinstance(provider_arg.sampler, ParentBased)


# ---------------------------------------------------------------------------
# Phase 3 – Body sanitization
# ---------------------------------------------------------------------------


class TestSanitizeBody:
    def test_none_level_returns_body_unchanged(self) -> None:
        from observe_kit.pii_rules import PiiLevel, sanitize_body

        body = {"password": "secret", "name": "Alice"}
        assert sanitize_body(body, PiiLevel.NONE) == body

    def test_drops_sensitive_keys(self) -> None:
        from observe_kit.pii_rules import PiiLevel, sanitize_body

        result = sanitize_body({"authorization": "Bearer token", "data": "ok"}, PiiLevel.BASIC)
        assert "authorization" not in result
        assert result["data"] == "ok"

    def test_masks_email_field(self) -> None:
        from observe_kit.pii_rules import PiiLevel, sanitize_body

        result = sanitize_body({"email": "alice@example.com"}, PiiLevel.BASIC)
        assert result["email"].startswith("a***")
        assert "example.com" in result["email"]

    def test_nested_dict_sanitized(self) -> None:
        from observe_kit.pii_rules import PiiLevel, sanitize_body

        body = {"user": {"email": "bob@example.com", "role": "admin"}}
        result = sanitize_body(body, PiiLevel.BASIC)
        assert "***" in result["user"]["email"]
        assert result["user"]["role"] == "admin"

    def test_list_items_sanitized(self) -> None:
        from observe_kit.pii_rules import PiiLevel, sanitize_body

        body = [{"email": "a@b.com"}, {"email": "c@d.com"}]
        result = sanitize_body(body, PiiLevel.BASIC)
        assert all("***" in item["email"] for item in result)

    def test_extra_drop_in_body(self) -> None:
        from observe_kit.pii_rules import PiiLevel, sanitize_body

        result = sanitize_body(
            {"api_key": "abc123", "name": "test"}, PiiLevel.BASIC, extra_drop=frozenset({"api_key"})
        )
        assert "api_key" not in result
        assert result["name"] == "test"

    def test_scalar_body_passthrough(self) -> None:
        from observe_kit.pii_rules import PiiLevel, sanitize_body

        assert sanitize_body("hello", PiiLevel.BASIC) == "hello"
        assert sanitize_body(42, PiiLevel.BASIC) == 42

    def test_none_value_in_dict(self) -> None:
        from observe_kit.pii_rules import PiiLevel, sanitize_body

        result = sanitize_body({"name": None, "role": "admin"}, PiiLevel.BASIC)
        assert result["name"] is None

    def test_cookie_header_dropped_in_body(self) -> None:
        from observe_kit.pii_rules import PiiLevel, sanitize_body

        result = sanitize_body({"cookie": "session=abc"}, PiiLevel.BASIC)
        assert "cookie" not in result


# ---------------------------------------------------------------------------
# Phase 3 – Audit before/after diff
# ---------------------------------------------------------------------------


class TestAuditBeforeAfter:
    def _make_audit_mock(self) -> tuple[Any, Mock]:
        """Return (mock AuditLog class, mock entry)."""
        mock_entry = Mock()
        mock_entry.id = 1
        mock_entry.object_type = None
        mock_entry.extra = {}
        mock_audit_log = Mock()
        mock_audit_log.objects.create = Mock(return_value=mock_entry)
        return mock_audit_log, mock_entry

    def test_before_after_stored_in_extra(self) -> None:
        from observe_kit.context import RequestContext, reset_request_context, set_request_context

        reset_request_context()
        set_request_context(RequestContext())

        mock_audit_log, mock_entry = self._make_audit_mock()
        with patch("observe_kit.audit.models.AuditLog", mock_audit_log):
            from observe_kit.audit.utils import audit

            audit(action="page.update", before={"title": "Old Title"}, after={"title": "New Title"})

        call_kwargs = mock_audit_log.objects.create.call_args[1]
        assert call_kwargs["extra"]["_before"] == {"title": "Old Title"}
        assert call_kwargs["extra"]["_after"] == {"title": "New Title"}

    def test_before_after_pii_sanitized(self) -> None:
        from observe_kit.context import RequestContext, reset_request_context, set_request_context
        from observe_kit.pii_rules import PiiConfig, set_pii_config

        reset_request_context()
        set_request_context(RequestContext())
        set_pii_config(PiiConfig({"audit": "BASIC"}))

        mock_audit_log, mock_entry = self._make_audit_mock()
        try:
            with patch("observe_kit.audit.models.AuditLog", mock_audit_log):
                from observe_kit.audit.utils import audit

                audit(
                    action="user.update",
                    before={"email": "alice@example.com", "role": "user"},
                    after={"email": "alice@example.com", "role": "admin"},
                )
        finally:
            set_pii_config(PiiConfig())

        call_kwargs = mock_audit_log.objects.create.call_args[1]
        assert "***" in call_kwargs["extra"]["_before"]["email"]
        assert call_kwargs["extra"]["_before"]["role"] == "user"
        assert call_kwargs["extra"]["_after"]["role"] == "admin"

    def test_no_before_after_not_in_extra(self) -> None:
        from observe_kit.context import RequestContext, reset_request_context, set_request_context

        reset_request_context()
        set_request_context(RequestContext())

        mock_audit_log, mock_entry = self._make_audit_mock()
        with patch("observe_kit.audit.models.AuditLog", mock_audit_log):
            from observe_kit.audit.utils import audit

            audit(action="page.view")

        call_kwargs = mock_audit_log.objects.create.call_args[1]
        assert "_before" not in call_kwargs["extra"]
        assert "_after" not in call_kwargs["extra"]

    def test_extra_sanitized_before_storage(self) -> None:
        from observe_kit.context import RequestContext, reset_request_context, set_request_context
        from observe_kit.pii_rules import PiiConfig, set_pii_config

        reset_request_context()
        set_request_context(RequestContext())
        set_pii_config(PiiConfig({"audit": "BASIC"}))

        mock_audit_log, mock_entry = self._make_audit_mock()
        try:
            with patch("observe_kit.audit.models.AuditLog", mock_audit_log):
                from observe_kit.audit.utils import audit

                audit(
                    action="form.submit", extra={"authorization": "Bearer secret", "name": "test"}
                )
        finally:
            set_pii_config(PiiConfig())

        call_kwargs = mock_audit_log.objects.create.call_args[1]
        assert "authorization" not in call_kwargs["extra"]
        assert call_kwargs["extra"]["name"] == "test"
