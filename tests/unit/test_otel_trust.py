"""Tests for the inbound trace-context trust gate in TraceContextMiddleware."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from observe_kit.context import reset_request_context
from observe_kit.otel.middleware import TraceContextMiddleware, _client_ip_matches_sources
from observe_kit.settings import get_observe_kit_settings

VALID_TRACEPARENT = "00-1234567890abcdef1234567890abcdef-1234567890abcdef-01"


@pytest.fixture
def rf() -> RequestFactory:
    return RequestFactory()


@pytest.fixture
def get_response() -> Mock:
    return Mock(return_value=HttpResponse(status=200))


@pytest.fixture(autouse=True)
def _reset_ctx() -> None:
    reset_request_context()
    yield
    reset_request_context()


# ---------------------------------------------------------------------------
# settings parsing
# ---------------------------------------------------------------------------


@override_settings(OBSERVE_KIT={})
def test_settings_default_is_secure() -> None:
    cfg = get_observe_kit_settings()
    assert cfg.trust_incoming_trace_context is False
    assert cfg.trusted_trace_sources == []


@override_settings(OBSERVE_KIT={"TRUST_INCOMING_TRACE_CONTEXT": True})
def test_settings_trust_can_be_enabled() -> None:
    assert get_observe_kit_settings().trust_incoming_trace_context is True


@override_settings(OBSERVE_KIT={"TRUSTED_TRACE_SOURCES": ["10.0.0.0/8", "192.168.1.5"]})
def test_settings_parse_trusted_sources_list() -> None:
    assert get_observe_kit_settings().trusted_trace_sources == ["10.0.0.0/8", "192.168.1.5"]


def test_settings_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBSERVE_KIT_TRUST_INCOMING_TRACE_CONTEXT", "true")
    monkeypatch.setenv("OBSERVE_KIT_TRUSTED_TRACE_SOURCES", "10.0.0.0/8, 172.16.0.0/12")
    with override_settings(OBSERVE_KIT={}):
        cfg = get_observe_kit_settings()
    assert cfg.trust_incoming_trace_context is True
    assert cfg.trusted_trace_sources == ["10.0.0.0/8", "172.16.0.0/12"]


# ---------------------------------------------------------------------------
# _client_ip_matches_sources helper
# ---------------------------------------------------------------------------


def test_helper_no_sources_never_matches() -> None:
    assert _client_ip_matches_sources("10.0.0.5", []) is False


def test_helper_missing_ip_never_matches() -> None:
    assert _client_ip_matches_sources(None, ["10.0.0.0/8"]) is False


def test_helper_cidr_match() -> None:
    assert _client_ip_matches_sources("10.0.0.5", ["10.0.0.0/8"]) is True
    assert _client_ip_matches_sources("203.0.113.7", ["10.0.0.0/8"]) is False


def test_helper_exact_ip_match() -> None:
    assert _client_ip_matches_sources("192.168.1.5", ["192.168.1.5"]) is True
    assert _client_ip_matches_sources("192.168.1.6", ["192.168.1.5"]) is False


def test_helper_ipv6_cidr() -> None:
    assert _client_ip_matches_sources("2001:db8::1", ["2001:db8::/32"]) is True


def test_helper_malformed_entries_skipped() -> None:
    # Mix of garbage and a real entry — should still match the real one.
    assert _client_ip_matches_sources("10.0.0.5", ["not-an-ip", "10.0.0.0/8", "999.999/8"]) is True


def test_helper_malformed_client_ip_never_matches() -> None:
    assert _client_ip_matches_sources("not-an-ip", ["10.0.0.0/8"]) is False


# ---------------------------------------------------------------------------
# middleware behavior — default (untrusted edge)
# ---------------------------------------------------------------------------


@override_settings(OBSERVE_KIT={})
def test_default_drops_inbound_traceparent(rf: RequestFactory, get_response: Mock) -> None:
    """By default, inbound traceparent must not influence the request's span."""
    middleware = TraceContextMiddleware(get_response)
    request = rf.get("/test/", HTTP_TRACEPARENT=VALID_TRACEPARENT)

    with patch("observe_kit.otel.middleware.extract") as mock_extract:
        middleware.process_request(request)
        mock_extract.assert_not_called()


@override_settings(OBSERVE_KIT={})
def test_default_starts_fresh_trace_id(rf: RequestFactory, get_response: Mock) -> None:
    """The resulting span's trace_id must not match the attacker-supplied one."""
    from observe_kit.context import get_request_context

    attacker_trace_id = "1234567890abcdef1234567890abcdef"
    middleware = TraceContextMiddleware(get_response)
    request = rf.get("/test/", HTTP_TRACEPARENT=f"00-{attacker_trace_id}-1234567890abcdef-01")
    middleware.process_request(request)
    context = get_request_context()
    assert context.trace_id is not None
    assert context.trace_id != attacker_trace_id


# ---------------------------------------------------------------------------
# middleware behavior — trust enabled globally
# ---------------------------------------------------------------------------


@override_settings(OBSERVE_KIT={"TRUST_INCOMING_TRACE_CONTEXT": True})
def test_trust_global_extracts(rf: RequestFactory, get_response: Mock) -> None:
    middleware = TraceContextMiddleware(get_response)
    request = rf.get("/test/", HTTP_TRACEPARENT=VALID_TRACEPARENT)

    with patch("observe_kit.otel.middleware.extract") as mock_extract:
        mock_extract.return_value = Mock()
        middleware.process_request(request)
        mock_extract.assert_called_once()


# ---------------------------------------------------------------------------
# middleware behavior — trust via CIDR allow-list
# ---------------------------------------------------------------------------


@override_settings(
    OBSERVE_KIT={"TRUST_INCOMING_TRACE_CONTEXT": True, "TRUSTED_TRACE_SOURCES": ["10.0.0.0/8"]}
)
def test_cidr_allowlist_extracts_for_matching_ip(rf: RequestFactory, get_response: Mock) -> None:
    """With trust enabled, an allow-listed source is honoured."""
    middleware = TraceContextMiddleware(get_response)
    request = rf.get("/test/", HTTP_TRACEPARENT=VALID_TRACEPARENT, REMOTE_ADDR="10.0.5.42")

    with patch("observe_kit.otel.middleware.extract") as mock_extract:
        mock_extract.return_value = Mock()
        middleware.process_request(request)
        mock_extract.assert_called_once()


@override_settings(
    OBSERVE_KIT={"TRUST_INCOMING_TRACE_CONTEXT": True, "TRUSTED_TRACE_SOURCES": ["10.0.0.0/8"]}
)
def test_cidr_allowlist_drops_for_non_matching_ip(rf: RequestFactory, get_response: Mock) -> None:
    """With trust enabled, a non-matching source is rejected by the allow-list."""
    middleware = TraceContextMiddleware(get_response)
    request = rf.get("/test/", HTTP_TRACEPARENT=VALID_TRACEPARENT, REMOTE_ADDR="203.0.113.7")

    with patch("observe_kit.otel.middleware.extract") as mock_extract:
        middleware.process_request(request)
        mock_extract.assert_not_called()


@override_settings(
    OBSERVE_KIT={
        "TRUST_INCOMING_TRACE_CONTEXT": True,
        "TRUSTED_TRACE_SOURCES": ["not-a-real-cidr", "10.0.0.0/8"],
    }
)
def test_malformed_cidr_does_not_break_middleware(rf: RequestFactory, get_response: Mock) -> None:
    """Malformed allow-list entries are silently skipped; valid entries still match."""
    middleware = TraceContextMiddleware(get_response)
    request = rf.get("/test/", HTTP_TRACEPARENT=VALID_TRACEPARENT, REMOTE_ADDR="10.0.0.5")

    with patch("observe_kit.otel.middleware.extract") as mock_extract:
        mock_extract.return_value = Mock()
        middleware.process_request(request)
        mock_extract.assert_called_once()


@override_settings(OBSERVE_KIT={"TRUSTED_TRACE_SOURCES": ["10.0.0.0/8"]})
def test_allowlist_ignored_when_global_flag_is_false(
    rf: RequestFactory, get_response: Mock
) -> None:
    """The allow-list never grants trust on its own — global flag is the master switch."""
    middleware = TraceContextMiddleware(get_response)
    request = rf.get("/test/", HTTP_TRACEPARENT=VALID_TRACEPARENT, REMOTE_ADDR="10.0.5.42")

    with patch("observe_kit.otel.middleware.extract") as mock_extract:
        middleware.process_request(request)
        mock_extract.assert_not_called()


@override_settings(OBSERVE_KIT={"TRUST_INCOMING_TRACE_CONTEXT": True, "TRUSTED_TRACE_SOURCES": []})
def test_global_trust_with_empty_allowlist_trusts_all(
    rf: RequestFactory, get_response: Mock
) -> None:
    """Empty allow-list means 'trust every source' when the global flag is on."""
    middleware = TraceContextMiddleware(get_response)
    request = rf.get("/test/", HTTP_TRACEPARENT=VALID_TRACEPARENT, REMOTE_ADDR="203.0.113.7")

    with patch("observe_kit.otel.middleware.extract") as mock_extract:
        mock_extract.return_value = Mock()
        middleware.process_request(request)
        mock_extract.assert_called_once()


# ---------------------------------------------------------------------------
# trusted-proxy-aware client IP resolution (codex P1 / qodo gap #2)
# ---------------------------------------------------------------------------


@override_settings(
    OBSERVE_KIT={
        "TRUST_INCOMING_TRACE_CONTEXT": True,
        "TRUSTED_TRACE_SOURCES": ["10.0.5.42"],
        "TRUSTED_PROXIES": ["192.168.1.1"],
    }
)
def test_allowlist_uses_xff_when_request_came_through_trusted_proxy(
    rf: RequestFactory, get_response: Mock
) -> None:
    """Originating client behind a trusted proxy must be evaluated, not the proxy IP."""
    middleware = TraceContextMiddleware(get_response)
    request = rf.get(
        "/test/",
        HTTP_TRACEPARENT=VALID_TRACEPARENT,
        REMOTE_ADDR="192.168.1.1",
        HTTP_X_FORWARDED_FOR="10.0.5.42",
    )

    with patch("observe_kit.otel.middleware.extract") as mock_extract:
        mock_extract.return_value = Mock()
        middleware.process_request(request)
        mock_extract.assert_called_once()


@override_settings(
    OBSERVE_KIT={
        "TRUST_INCOMING_TRACE_CONTEXT": True,
        "TRUSTED_TRACE_SOURCES": ["10.0.5.42"],
        "TRUSTED_PROXIES": [],
    }
)
def test_allowlist_ignores_xff_when_no_trusted_proxies(
    rf: RequestFactory, get_response: Mock
) -> None:
    """Without TRUSTED_PROXIES, an XFF header from an untrusted edge must be ignored."""
    middleware = TraceContextMiddleware(get_response)
    request = rf.get(
        "/test/",
        HTTP_TRACEPARENT=VALID_TRACEPARENT,
        REMOTE_ADDR="203.0.113.7",
        HTTP_X_FORWARDED_FOR="10.0.5.42",
    )

    with patch("observe_kit.otel.middleware.extract") as mock_extract:
        middleware.process_request(request)
        mock_extract.assert_not_called()


# ---------------------------------------------------------------------------
# X-Trace-Id response header contract (qodo gap #3)
# ---------------------------------------------------------------------------


@override_settings(OBSERVE_KIT={})
def test_default_path_emits_fresh_x_trace_id_header(rf: RequestFactory, get_response: Mock) -> None:
    """When inbound trace is dropped, X-Trace-Id must reflect the fresh server-generated trace."""
    from django.http import HttpResponse

    attacker_trace_id = "deadbeefdeadbeefdeadbeefdeadbeef"
    middleware = TraceContextMiddleware(get_response)
    request = rf.get("/test/", HTTP_TRACEPARENT=f"00-{attacker_trace_id}-1234567890abcdef-01")
    middleware.process_request(request)
    response = middleware.process_response(request, HttpResponse(status=200))

    header = response.get("X-Trace-Id")
    assert header is not None
    assert len(header) == 32
    assert header != attacker_trace_id


# ---------------------------------------------------------------------------
# strict bool parsing (qodo bug #4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env_value,expected",
    [
        # Canonical truthy strings should enable trust.
        ("true", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("t", True),
        # Canonical falsy strings — incl. previously-ambiguous "0"/"no"/"off".
        ("false", False),
        ("False", False),
        ("0", False),
        ("no", False),
        ("off", False),
        ("", False),
        # Unrecognized strings fall back to the secure default (False).
        ("garbage", False),
        ("maybe", False),
    ],
)
def test_strict_bool_env_parsing(
    monkeypatch: pytest.MonkeyPatch, env_value: str, expected: bool
) -> None:
    monkeypatch.setenv("OBSERVE_KIT_TRUST_INCOMING_TRACE_CONTEXT", env_value)
    with override_settings(OBSERVE_KIT={}):
        cfg = get_observe_kit_settings()
    assert cfg.trust_incoming_trace_context is expected
