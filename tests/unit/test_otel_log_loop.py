"""Records produced while exporting must not be exported again.

At DEBUG, one OTLP export logs (urllib3's request line; retry and error records when
the collector is down). Fed back into the OTel log handler, each export caused the
next one, forever.

Run in subprocesses against local fake collectors. The batch workers are slowed to a
crawl and ``force_flush()`` drives every export, so the assertions are on content and
do not depend on timing.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from concurrent import futures
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import grpc
import pytest
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2, logs_service_pb2_grpc
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2, trace_service_pb2_grpc

# A healthy run takes a few seconds and a looping one never ends.
_HANG_TIMEOUT = 90

_APP = """
import logging, sys, time
from opentelemetry import context, trace
from opentelemetry._logs import get_logger_provider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

http_endpoint, grpc_endpoint, mode = sys.argv[1:]
logging.basicConfig(level="DEBUG")
if mode == "no-private-key":
    # The SDK modules above already hold their own reference to the key.
    import opentelemetry.sdk._logs, opentelemetry.sdk.trace.export
    del context._SUPPRESS_INSTRUMENTATION_KEY

from observe_kit import init_tracing

init_tracing(service_name="loop-test", endpoint=http_endpoint)
if grpc_endpoint:
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    get_logger_provider().add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=grpc_endpoint, insecure=True))
    )
    trace.get_tracer_provider().add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=grpc_endpoint, insecure=True))
    )

logging.getLogger("app").debug("app-record")
logging.getLogger("urllib3.connectionpool").debug("app-urllib3-record")
with trace.get_tracer("app").start_as_current_span("app-span"):
    pass

if mode == "worker":
    time.sleep(2)  # many ticks of the batch worker, each one a chance to loop
else:
    # Span export first: what it logs must not end up in the log queue.
    trace.get_tracer_provider().force_flush()
    get_logger_provider().force_flush()
    get_logger_provider().force_flush()
"""

# Logger names (OTel instrumentation scopes) that the export path logs under.
_EXPORT_PATH = ("opentelemetry", "urllib3", "requests", "grpc")


class _Collector:
    def __init__(self) -> None:
        self.log_requests: list[Any] = []
        self.span_names: set[str] = set()

    def add_logs(self, request: Any) -> None:
        self.log_requests.append(request)

    def add_spans(self, request: Any) -> None:
        for resource in request.resource_spans:
            for scope in resource.scope_spans:
                self.span_names.update(span.name for span in scope.spans)

    def batches(self) -> list[Any]:
        """Distinct log requests; a retry resends the same one."""
        return list({r.SerializeToString(): r for r in self.log_requests}.values())

    def records(self) -> list[tuple[str, str]]:
        return [
            (scope.scope.name, record.body.string_value)
            for request in self.batches()
            for resource in request.resource_logs
            for scope in resource.scope_logs
            for record in scope.log_records
        ]


@pytest.fixture
def http_collector(request: pytest.FixtureRequest) -> Iterator[tuple[str, _Collector]]:
    status = getattr(request, "param", 200)
    collector = _Collector()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/v1/logs":
                collector.add_logs(logs_service_pb2.ExportLogsServiceRequest.FromString(body))
            else:
                collector.add_spans(trace_service_pb2.ExportTraceServiceRequest.FromString(body))
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", collector
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def grpc_collector(request: pytest.FixtureRequest) -> Iterator[tuple[str, _Collector]]:
    available = getattr(request, "param", True)
    collector = _Collector()

    class Logs(logs_service_pb2_grpc.LogsServiceServicer):
        def Export(self, request: Any, context: Any) -> Any:
            collector.add_logs(request)
            if not available:
                context.abort(grpc.StatusCode.UNAVAILABLE, "collector down")
            return logs_service_pb2.ExportLogsServiceResponse()

    class Traces(trace_service_pb2_grpc.TraceServiceServicer):
        def Export(self, request: Any, context: Any) -> Any:
            collector.add_spans(request)
            if not available:
                context.abort(grpc.StatusCode.UNAVAILABLE, "collector down")
            return trace_service_pb2.ExportTraceServiceResponse()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    logs_service_pb2_grpc.add_LogsServiceServicer_to_server(Logs(), server)
    trace_service_pb2_grpc.add_TraceServiceServicer_to_server(Traces(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        yield f"127.0.0.1:{port}", collector
    finally:
        server.stop(None).wait()


def _run_app(
    http_endpoint: str, grpc_endpoint: str = "", mode: str = "flush"
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "DJANGO_"))}
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1"
    # Shortens the SDK's retry window when the collector is down.
    env["OTEL_EXPORTER_OTLP_TIMEOUT"] = "2"
    delay = "100" if mode == "worker" else "600000"
    env["OTEL_BLRP_SCHEDULE_DELAY"] = env["OTEL_BSP_SCHEDULE_DELAY"] = delay
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    result = subprocess.run(
        [sys.executable, "-c", _APP, http_endpoint, grpc_endpoint, mode],
        env=env,
        capture_output=True,
        text=True,
        timeout=_HANG_TIMEOUT,
    )
    assert result.returncode == 0, result.stderr
    return result


def _assert_only_app_records_exported(collector: _Collector) -> None:
    records = collector.records()
    assert ("app", "app-record") in records
    # The application's own urllib3 records, logged outside an export, are kept.
    from_export_path = [r for r in records if r[0].split(".")[0] in _EXPORT_PATH]
    assert from_export_path == [("urllib3.connectionpool", "app-urllib3-record")]
    assert "app-span" in collector.span_names


@pytest.mark.parametrize("mode", ["flush", "worker"])
def test_http_export_records_are_not_exported_again(
    http_collector: tuple[str, _Collector], mode: str
) -> None:
    endpoint, collector = http_collector

    result = _run_app(endpoint, mode=mode)

    _assert_only_app_records_exported(collector)
    if mode == "flush":
        assert len(collector.log_requests) == 1
    # Other handlers still receive what the export path logs.
    assert "POST /v1/logs" in result.stderr


@pytest.mark.parametrize("http_collector", [503, 400], indirect=True)
def test_failing_http_collector_does_not_loop(http_collector: tuple[str, _Collector]) -> None:
    endpoint, collector = http_collector

    result = _run_app(endpoint)

    _assert_only_app_records_exported(collector)
    # Retries resend the same batch; there is never a second one.
    assert len(collector.batches()) == 1
    assert "opentelemetry.exporter.otlp.proto.http" in result.stderr


@pytest.mark.parametrize("grpc_collector", [True, False], ids=["up", "down"], indirect=True)
def test_grpc_export_records_are_not_exported_again(
    http_collector: tuple[str, _Collector], grpc_collector: tuple[str, _Collector]
) -> None:
    endpoint, _ = http_collector
    grpc_endpoint, collector = grpc_collector

    _run_app(endpoint, grpc_endpoint)

    _assert_only_app_records_exported(collector)
    assert len(collector.batches()) == 1


def test_without_the_private_sdk_key_loggers_of_the_export_path_are_skipped(
    http_collector: tuple[str, _Collector],
) -> None:
    endpoint, collector = http_collector

    result = _run_app(endpoint, mode="no-private-key")

    # The fallback cannot tell the application's urllib3 records from the exporter's.
    assert [r for r in collector.records() if r[0].split(".")[0] in _EXPORT_PATH] == []
    assert ("app", "app-record") in collector.records()
    assert len(collector.log_requests) == 1
    assert "app-urllib3-record" in result.stderr
    assert "POST /v1/logs" in result.stderr
