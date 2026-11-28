"""No email or credential-query value may reach an exported span above the NONE level."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from django.urls import ResolverMatch
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from observe_kit.context import get_request_context, reset_request_context
from observe_kit.drf.integration import DRFIntegrationMiddleware
from observe_kit.otel.config import SpanNamer, enrich_span
from observe_kit.otel.middleware import TraceContextMiddleware
from observe_kit.pii_rules import PiiConfig, get_pii_config, set_pii_config

LEAKS = ("alice@", "alice%40", "alice%2540", "s3cr3t")
MESSAGE = "dup user alice@corp.example https://x.test/cb?token=s3cr3t"
ABOVE_NONE = ["BASIC", "SENSITIVE"]
Traces = tuple[InMemorySpanExporter, TracerProvider]


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    original = get_pii_config()
    reset_request_context()
    yield
    set_pii_config(original)
    reset_request_context()


@pytest.fixture
def traces() -> Iterator[Traces]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with patch(
        "observe_kit.otel.middleware.trace.get_tracer",
        side_effect=lambda name: provider.get_tracer(name),
    ):
        yield exporter, provider


def _level(level: str) -> None:
    set_pii_config(PiiConfig({"otel": level}))


def _assert_clean(span: ReadableSpan) -> None:
    dumped = repr(
        (
            span.name,
            dict(span.attributes or {}),
            span.status.description,
            [(e.name, dict(e.attributes or {})) for e in span.events],
        )
    )
    for leak in LEAKS:
        assert leak not in dumped, f"{leak!r} leaked: {dumped}"


def _run(
    traces: Traces, path: str = "/ok/", exception: Any = None, status: int = 200
) -> ReadableSpan:
    middleware = TraceContextMiddleware(lambda r: HttpResponse())
    request = RequestFactory().get(path)
    middleware.process_request(request)
    if exception is not None:
        middleware.process_exception(request, exception)
    response = middleware.process_response(request, HttpResponse(status=status))
    (span,) = traces[0].get_finished_spans()
    assert response["X-Trace-Id"] == format(span.context.trace_id, "032x")
    return span


def _raised(exc: BaseException, cause: BaseException | None = None) -> BaseException:
    try:
        raise exc from cause
    except BaseException as caught:  # noqa: BLE001
        return caught


@pytest.mark.parametrize("level", ABOVE_NONE)
@pytest.mark.parametrize("status", [403, 404, 500])
def test_view_exception_is_scrubbed(traces: Traces, level: str, status: int) -> None:
    _level(level)
    span = _run(traces, exception=_raised(RuntimeError(MESSAGE)), status=status)
    _assert_clean(span)
    (event,) = span.events
    assert event.name == "exception"
    assert event.attributes["exception.type"] == "RuntimeError"
    assert "a***@corp.example" in event.attributes["exception.message"]
    assert "token=REDACTED" in event.attributes["exception.message"]
    assert "Traceback (most recent call last)" in event.attributes["exception.stacktrace"]
    assert event.attributes["exception.escaped"] == "False"
    assert span.status.status_code == StatusCode.ERROR


@pytest.mark.parametrize("level", ABOVE_NONE)
def test_exception_chain_is_scrubbed(traces: Traces, level: str) -> None:
    _level(level)
    inner = _raised(KeyError("alice@corp.example"))
    outer = _raised(ValueError("clean outer"), cause=inner)
    span = _run(traces, exception=outer, status=500)
    _assert_clean(span)
    assert "direct cause" in span.events[0].attributes["exception.stacktrace"]


@pytest.mark.skipif(sys.version_info < (3, 11), reason="notes and groups need 3.11")
def test_exception_notes_and_groups_are_scrubbed(traces: Traces) -> None:
    _level("BASIC")
    leaf = ValueError("leaf alice@corp.example")
    leaf.add_note("note https://x.test/cb?token=s3cr3t")  # type: ignore[attr-defined]
    group = ExceptionGroup("batch alice@corp.example", [leaf])  # type: ignore[name-defined]  # noqa: F821
    span = _run(traces, exception=_raised(group), status=500)
    _assert_clean(span)
    assert "token=REDACTED" in span.events[0].attributes["exception.stacktrace"]


def test_exception_type_name_is_scrubbed(traces: Traces) -> None:
    _level("BASIC")
    cls = type("alice@corp.example", (Exception,), {"__module__": "builtins"})
    _assert_clean(_run(traces, exception=_raised(cls("x")), status=500))


class _Unprintable(Exception):
    def __str__(self) -> str:
        raise RuntimeError(MESSAGE)


@pytest.mark.parametrize("level", ABOVE_NONE)
def test_unprintable_exception_never_raises(traces: Traces, level: str) -> None:
    _level(level)
    span = _run(traces, exception=_raised(_Unprintable()), status=500)
    _assert_clean(span)
    assert span.events[0].attributes["exception.message"] == "[Filtered]"
    assert span.status.status_code == StatusCode.ERROR


@pytest.mark.parametrize("level", ABOVE_NONE)
def test_scrub_failure_never_emits_raw(traces: Traces, level: str) -> None:
    _level(level)
    with patch("observe_kit.log_sanitize._scrub_text", side_effect=RuntimeError("boom")):
        span = _run(
            traces,
            "/u/alice@corp.example/?q=alice@corp.example",
            exception=_raised(RuntimeError(MESSAGE)),
            status=500,
        )
    _assert_clean(span)
    assert span.attributes["url.path"] == "[Filtered]"
    assert span.events[0].attributes["exception.message"] == "[Filtered]"


@pytest.mark.parametrize("level", ABOVE_NONE)
@pytest.mark.parametrize(
    "path",
    [
        "/users/alice@corp.example/",
        "/users/alice%40corp.example/",
        "/users/alice%2540corp.example/",
        "/ok/?q=alice@corp.example",
        "/ok/?alice@corp.example=1",
        "/ok/?next=%2Fcb%3Ftoken%3Ds3cr3t",
        "/ok/?q=alice%2540corp.example",
        "/users/alice@corp.example/?token=s3cr3t",
    ],
)
def test_request_paths_and_queries_are_scrubbed(traces: Traces, level: str, path: str) -> None:
    _level(level)
    _assert_clean(_run(traces, path))


def test_clean_path_encoding_is_unchanged(traces: Traces) -> None:
    _level("SENSITIVE")
    span = _run(traces, "/files/a%20b/caf%C3%A9/")
    assert span.name == "GET /files/a b/café/"
    assert span.attributes["url.path"] == "/files/a b/café/"
    assert span.attributes["http.target"] == "/files/a%20b/caf%C3%A9/"


def test_path_keeps_context_around_masked_email(traces: Traces) -> None:
    _level("BASIC")
    span = _run(traces, "/users/alice@corp.example/orders/")
    assert span.name == "GET /users/a***@corp.example/orders/"
    assert span.attributes["url.path"] == "/users/a***@corp.example/orders/"
    assert span.attributes["http.target"] == "/users/a***@corp.example/orders/"


@pytest.mark.parametrize("level", ABOVE_NONE)
def test_invalid_host_setup_failure_is_scrubbed(traces: Traces, level: str) -> None:
    """get_host() raises mid-setup, and the span context manager exits with that error."""
    _level(level)
    middleware = TraceContextMiddleware(lambda r: HttpResponse())
    request = RequestFactory().get("/ok/", HTTP_HOST="alice@corp.example")
    with override_settings(ALLOWED_HOSTS=["testserver"]):
        middleware.process_request(request)
    (span,) = traces[0].get_finished_spans()
    _assert_clean(span)
    (event,) = span.events
    assert event.attributes["exception.type"].endswith("DisallowedHost")
    assert event.attributes["exception.escaped"] == "False"
    assert span.status.status_code == StatusCode.ERROR
    assert span.status.description.startswith("DisallowedHost: ")


@pytest.mark.parametrize("level", ABOVE_NONE)
def test_enrich_span_scrubs_context_values(traces: Traces, level: str) -> None:
    _level(level)
    context = get_request_context()
    context.path = "/u/alice@corp.example"
    context.route = "/cb?token=s3cr3t"
    context.tenant_id = "alice@corp.example"
    context.user_id = "alice@corp.example"
    context.framework = "alice@corp.example"
    context.db_queries = 3
    with traces[1].get_tracer("t").start_as_current_span("s") as span:
        enrich_span(span)
    (finished,) = traces[0].get_finished_spans()
    _assert_clean(finished)
    assert finished.attributes["db.query.count"] == 3
    assert context.user_id == "alice@corp.example"  # only the span copy is scrubbed


@pytest.mark.parametrize("level", ABOVE_NONE)
def test_span_renames_are_scrubbed(traces: Traces, level: str) -> None:
    _level(level)
    tracer = traces[1].get_tracer("t")
    with tracer.start_as_current_span("s") as span:
        get_request_context().route = "/u/alice@corp.example"
        SpanNamer().update_span_name(span, None)
    request = RequestFactory().get("/")
    with tracer.start_as_current_span("s") as span:
        request._observe_kit_span = span  # type: ignore[attr-defined]
        with patch(
            "observe_kit.drf.integration.detect_drf_route", return_value="drf.alice@corp.example"
        ):
            DRFIntegrationMiddleware(lambda r: HttpResponse()).process_view(request, None, (), {})
    for finished in traces[0].get_finished_spans():
        _assert_clean(finished)


def test_drf_name_unchanged(traces: Traces) -> None:
    _level("SENSITIVE")
    middleware = TraceContextMiddleware(lambda r: HttpResponse())
    request = RequestFactory().get("/")
    middleware.process_request(request)
    with patch("observe_kit.drf.integration.detect_drf_route", return_value="drf.UserViewSet.list"):
        DRFIntegrationMiddleware(lambda r: HttpResponse()).process_view(request, None, (), {})
    middleware.process_response(request, HttpResponse())
    (span,) = traces[0].get_finished_spans()
    assert span.name == "drf.UserViewSet.list"
    assert span.attributes["http.route"] == "drf.UserViewSet.list"


@pytest.mark.parametrize(
    "route",
    [
        "users/<str:email>/",
        "^users/(?P<pk>[^/.]+)/$",
        r"^users/(?P<token>[^/.]+)\.(?P<format>[a-z0-9]+)/?$",
        "api/^items/(?P<code>[^/.]+)/$",
    ],
)
def test_route_patterns_are_unchanged(traces: Traces, route: str) -> None:
    _level("SENSITIVE")
    request = RequestFactory().get("/")
    request.resolver_match = ResolverMatch(lambda r: None, (), {}, route=route)
    middleware = TraceContextMiddleware(lambda r: HttpResponse())
    middleware.process_request(request)
    middleware.process_response(request, HttpResponse())
    (span,) = traces[0].get_finished_spans()
    assert span.name == f"GET {route}"
    assert span.attributes["http.route"] == route


@pytest.mark.parametrize("level", ["NONE", *ABOVE_NONE])
def test_wagtail_span_context_manager(traces: Traces, level: str) -> None:
    """The SDK records exceptions itself when ``start_as_current_span`` exits."""
    hooks = pytest.importorskip("observe_kit.wagtail_integration.wagtail_hooks")
    _level(level)

    def boom(page: Any, request: Any, span: Any) -> None:
        raise RuntimeError(MESSAGE)

    with patch.object(hooks, "tracer", traces[1].get_tracer("w")):
        with pytest.raises(RuntimeError, match="s3cr3t"):  # the caller still gets the original
            hooks._with_span("wagtail alice@corp.example", boom)(None, None)
    (span,) = traces[0].get_finished_spans()
    assert span.status.status_code == StatusCode.ERROR
    assert len(span.events) == 1
    if level == "NONE":
        assert span.name == "wagtail alice@corp.example"
        assert span.status.description == f"RuntimeError: {MESSAGE}"
        assert span.events[0].attributes["exception.message"] == MESSAGE
    else:
        _assert_clean(span)
        assert span.status.description.startswith("RuntimeError: dup user a***@")
        assert span.events[0].attributes["exception.escaped"] == "False"


def test_none_level_is_unchanged(traces: Traces) -> None:
    _level("NONE")
    span = _run(
        traces,
        "/users/alice@corp.example/?q=alice@corp.example&token=s3cr3t",
        exception=_raised(RuntimeError(MESSAGE)),
        status=404,
    )
    assert span.name == "GET /users/alice@corp.example/"
    assert span.attributes["url.path"] == "/users/alice@corp.example/"
    assert span.attributes["http.target"] == (
        "/users/alice@corp.example/?q=alice%40corp.example&token=s3cr3t"
    )
    assert span.status.description == MESSAGE
    assert span.events[0].attributes["exception.message"] == MESSAGE
    assert MESSAGE in span.events[0].attributes["exception.stacktrace"]


def test_clean_exception_event_unchanged(traces: Traces) -> None:
    _level("SENSITIVE")
    span = _run(traces, exception=_raised(ValueError("plain failure")), status=500)
    assert span.events[0].attributes["exception.message"] == "plain failure"
    assert "ValueError: plain failure" in span.events[0].attributes["exception.stacktrace"]
    assert span.events[0].attributes["exception.type"] == "ValueError"
