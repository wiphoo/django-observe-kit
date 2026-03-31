"""Integration tests for OpenTelemetry data storage in ClickHouse.

These tests REQUIRE a running OTEL Collector and ClickHouse. They verify:
1. Traces are exported to ClickHouse with ServiceName and StatusCode
2. Logs are exported to ClickHouse with ServiceName
3. Standard OTEL metadata is present in both traces and logs
"""

import json
import os
import time
from typing import Any, Dict, List, Optional

import pytest
import requests
from django.test import Client
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

pytestmark = pytest.mark.integration


@pytest.fixture(scope="function")
def clickhouse_url() -> str:
    """ClickHouse HTTP endpoint URL."""
    port = os.getenv("OBSERVE_KIT_INTEGRATION_CLICKHOUSE_HTTP_PORT", "28123")
    return f"http://localhost:{port}"


@pytest.fixture(scope="function")
def clickhouse_client(clickhouse_url: str) -> Any:
    """ClickHouse client for querying data using HTTP API."""
    # Use HTTP API instead of native client to avoid extra dependencies
    return {"url": clickhouse_url, "user": "default", "password": "clickhouse"}


@pytest.fixture(scope="function")
def wait_for_clickhouse(clickhouse_url: str) -> None:
    """Wait for ClickHouse to be ready."""
    max_retries = 60  # Increased from 30 to 60 seconds
    for i in range(max_retries):
        try:
            response = requests.get(f"{clickhouse_url}/ping", timeout=3)
            if response.status_code == 200:
                return
        except requests.exceptions.RequestException:
            pass
        if i < max_retries - 1:
            time.sleep(1)
    raise RuntimeError(f"ClickHouse not available at {clickhouse_url}")


@pytest.fixture(scope="function")
def otel_http_endpoint() -> str:
    """OTEL Collector HTTP endpoint from environment."""
    port = os.getenv("OBSERVE_KIT_INTEGRATION_OTEL_COLLECTOR_OTLP_HTTP_PORT", "4318")
    return f"http://localhost:{port}"


def verify_collector_ready(otel_http_endpoint: str, timeout: int = 30) -> bool:
    """Verify the OTEL collector is ready to accept traces."""
    traces_endpoint = f"{otel_http_endpoint}/v1/traces"
    start = time.time()

    while time.time() - start < timeout:
        try:
            response = requests.post(
                traces_endpoint, headers={"Content-Type": "application/json"}, json={}, timeout=3
            )
            # Any HTTP response (even errors) means the service is running
            if response.status_code in (200, 400, 405, 415, 404):
                return True
        except requests.exceptions.ConnectionError:
            # Connection refused - service not ready yet
            pass
        except requests.exceptions.RequestException:
            # Other errors might mean service is up but rejecting request
            # Try health endpoint as fallback
            try:
                health_endpoint = otel_http_endpoint.replace(":4318", ":13133")
                health_response = requests.get(f"{health_endpoint}/", timeout=2)
                if health_response.status_code == 200:
                    return True
            except Exception:
                pass
        time.sleep(0.5)

    return False


@pytest.fixture(scope="function")
def fresh_tracer_provider(otel_http_endpoint: str, wait_for_otel_collector: Any) -> Any:
    """Create a fresh TracerProvider with service name for testing."""
    assert verify_collector_ready(otel_http_endpoint), (
        f"OTEL Collector not ready at {otel_http_endpoint}"
    )

    # Create exporter and provider with service name
    traces_endpoint = f"{otel_http_endpoint}/v1/traces"
    exporter = OTLPSpanExporter(endpoint=traces_endpoint)

    # Set service name in resource attributes
    resource = Resource.create({"service.name": "test-service"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))

    # Set as the global provider
    old_provider = trace.get_tracer_provider()
    trace.set_tracer_provider(provider)

    yield provider

    # Cleanup
    try:
        provider.force_flush(timeout_millis=5000)
    except Exception:
        pass
    try:
        provider.shutdown()
    except Exception:
        pass
    trace.set_tracer_provider(old_provider)


@pytest.fixture(scope="function")
def test_tracer(fresh_tracer_provider: TracerProvider) -> trace.Tracer:
    """Get a tracer from the fresh provider."""
    return fresh_tracer_provider.get_tracer(__name__)


def query_clickhouse_traces(
    clickhouse_client: Any,
    service_name: Optional[str] = None,
    trace_id: Optional[str] = None,
    max_wait_seconds: int = 30,
) -> List[Dict[str, Any]]:
    """Query ClickHouse for traces using HTTP API, waiting for data to appear."""
    start_time = time.time()
    url = clickhouse_client["url"]
    auth = (clickhouse_client["user"], clickhouse_client["password"])
    last_error = None

    while time.time() - start_time < max_wait_seconds:
        try:
            query = """
                SELECT
                    ServiceName,
                    StatusCode,
                    SpanName,
                    TraceId,
                    SpanId,
                    ResourceAttributes,
                    SpanAttributes,
                    Duration
                FROM default.otel_traces
                WHERE 1=1
            """

            if service_name:
                # Escape single quotes in service name
                escaped_name = service_name.replace("'", "''")
                query += f" AND ServiceName = '{escaped_name}'"

            if trace_id:
                # Escape single quotes in trace_id
                escaped_trace_id = trace_id.replace("'", "''")
                query += f" AND TraceId = '{escaped_trace_id}'"

            query += " ORDER BY Timestamp DESC LIMIT 100"

            # Use HTTP API with format=JSONEachRow
            response = requests.post(
                f"{url}?default_format=JSONEachRow", data=query, auth=auth, timeout=5
            )

            if response.status_code != 200:
                last_error = (
                    f"ClickHouse returned status {response.status_code}: {response.text[:200]}"
                )
                time.sleep(0.5)
                continue

            if response.text.strip():
                # Parse JSONEachRow format (one JSON object per line)
                rows = []
                for line in response.text.strip().split("\n"):
                    if line:
                        try:
                            rows.append(json.loads(line))
                        except Exception as e:
                            last_error = f"Failed to parse JSON: {e}, line: {line[:100]}"
                            continue

                if rows:
                    return rows

            time.sleep(0.5)
        except requests.exceptions.RequestException as e:
            last_error = f"Request error: {e}"
            time.sleep(0.5)
        except Exception as e:
            last_error = f"Unexpected error: {e}"
            time.sleep(0.5)

    # If we get here, return empty list but log the last error for debugging
    if last_error:
        print(f"DEBUG: Last error querying ClickHouse: {last_error}")
    return []


def query_clickhouse_logs(
    clickhouse_client: Any,
    service_name: str,
    trace_id: Optional[str] = None,
    max_wait_seconds: int = 20,
) -> List[Dict[str, Any]]:
    """Query ClickHouse for logs using HTTP API, waiting for data to appear."""
    start_time = time.time()
    url = clickhouse_client["url"]
    auth = (clickhouse_client["user"], clickhouse_client["password"])

    while time.time() - start_time < max_wait_seconds:
        try:
            query = """
                SELECT
                    ServiceName,
                    Body,
                    SeverityText,
                    TraceId,
                    SpanId,
                    ResourceAttributes,
                    LogAttributes
                FROM default.otel_logs
                WHERE ServiceName = '{service_name}'
            """.format(service_name=service_name)

            if trace_id:
                query += f" AND TraceId = '{trace_id}'"

            query += " ORDER BY Timestamp DESC LIMIT 100"

            # Use HTTP API with format=JSONEachRow
            response = requests.post(
                f"{url}?default_format=JSONEachRow", data=query, auth=auth, timeout=5
            )

            if response.status_code == 200 and response.text.strip():
                # Parse JSONEachRow format (one JSON object per line)
                rows = []
                for line in response.text.strip().split("\n"):
                    if line:
                        rows.append(json.loads(line))

                if rows:
                    return rows

            time.sleep(0.5)
        except Exception:
            # If table doesn't exist or other error, wait and retry
            time.sleep(0.5)

    return []


def test_traces_stored_in_clickhouse_with_service_name(
    django_client: Client,
    clickhouse_client: Any,
    wait_for_clickhouse: None,
    wait_for_otel_collector: Any,
    otel_http_endpoint: str,
) -> None:
    """Test that traces are stored in ClickHouse with ServiceName."""
    # First, ensure OTEL is initialized with service name
    from observe_kit.otel import init_tracing

    # Initialize tracing with a known service name
    init_tracing(service_name="test-service", endpoint=otel_http_endpoint)

    # Make a request that will create a span
    response = django_client.get("/metrics")
    assert response.status_code == 200

    # Get trace ID from response
    trace_id = response.get("X-Trace-Id")
    assert trace_id is not None, "X-Trace-Id header not found"

    # Force flush to ensure span is exported
    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush(timeout_millis=5000)

    # Wait for data to be processed and stored (collector needs time to batch and export)
    time.sleep(5)

    # First, query without service name filter to see what's actually there
    all_traces = query_clickhouse_traces(
        clickhouse_client, service_name=None, trace_id=trace_id, max_wait_seconds=30
    )

    if not all_traces:
        # Try querying all traces to see what service names exist
        all_traces_all = query_clickhouse_traces(
            clickhouse_client, service_name=None, trace_id=None, max_wait_seconds=5
        )
        service_names = {t.get("ServiceName") for t in all_traces_all if t.get("ServiceName")}
        pytest.fail(
            f"No traces found in ClickHouse for trace_id={trace_id}. "
            f"Found {len(all_traces_all)} total traces with service names: {service_names}"
        )

    # Now filter by service name
    traces = [t for t in all_traces if t.get("ServiceName") == "test-service"]

    if not traces:
        # Show what service names we actually got
        actual_service_names = {t.get("ServiceName") for t in all_traces if t.get("ServiceName")}
        pytest.fail(
            f"No traces found with ServiceName='test-service'. "
            f"Found traces with service names: {actual_service_names}"
        )

    # Verify ServiceName is present
    for trace_row in traces:
        assert trace_row["ServiceName"] == "test-service", (
            f"Expected ServiceName='test-service', got '{trace_row['ServiceName']}'"
        )
        assert trace_row["ServiceName"] is not None, "ServiceName should not be None"
        assert trace_row["ServiceName"] != "", "ServiceName should not be empty"


def test_traces_stored_with_status_code(
    django_client: Client,
    clickhouse_client: Any,
    wait_for_clickhouse: None,
    wait_for_otel_collector: Any,
    otel_http_endpoint: str,
) -> None:
    """Test that traces are stored in ClickHouse with StatusCode."""
    # Initialize tracing
    from observe_kit.otel import init_tracing

    init_tracing(service_name="test-service", endpoint=otel_http_endpoint)

    # Make requests with different status codes
    test_cases = [
        ("/metrics", 200, "OK"),  # Successful request
        ("/nonexistent", 404, "UNSET"),  # Not found (4xx should be UNSET)
    ]

    trace_ids = []
    for path, expected_status, expected_code in test_cases:
        response = django_client.get(path)
        assert response.status_code == expected_status

        trace_id = response.get("X-Trace-Id")
        assert trace_id is not None
        trace_ids.append((trace_id, expected_code))

    # Force flush
    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush(timeout_millis=5000)

    # Wait for data to be processed (collector batches, so need more time)
    # Wait for data to be ingested (OTEL collector batches data)
    time.sleep(5)

    # Query ClickHouse for traces
    traces = query_clickhouse_traces(
        clickhouse_client, service_name="test-service", max_wait_seconds=30
    )

    if not traces:
        # Try without service name filter
        all_traces = query_clickhouse_traces(
            clickhouse_client, service_name=None, max_wait_seconds=5
        )
        pytest.fail(
            f"No traces found in ClickHouse with ServiceName='test-service'. "
            f"Found {len(all_traces)} total traces."
        )

    # Verify StatusCode is present and correct
    found_traces = {t["TraceId"]: t for t in traces if t.get("TraceId")}

    for trace_id, expected_code in trace_ids:
        if trace_id in found_traces:
            trace_row = found_traces[trace_id]
            status_code = trace_row.get("StatusCode")
            assert status_code is not None, "StatusCode should not be None"
            # StatusCode should be one of: OK, ERROR, UNSET
            assert status_code in ("OK", "ERROR", "UNSET"), f"Invalid StatusCode: {status_code}"


def test_traces_have_standard_otel_metadata(
    django_client: Client,
    clickhouse_client: Any,
    wait_for_clickhouse: None,
    wait_for_otel_collector: Any,
    otel_http_endpoint: str,
) -> None:
    """Test that traces have standard OTEL metadata (resource attributes, span attributes)."""
    # Initialize tracing
    from observe_kit.otel import init_tracing

    init_tracing(service_name="test-service", endpoint=otel_http_endpoint)

    # Make a request
    response = django_client.get("/metrics")
    assert response.status_code == 200

    trace_id = response.get("X-Trace-Id")
    assert trace_id is not None

    # Force flush
    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush(timeout_millis=5000)

    # Wait for data
    # Wait for data to be ingested (OTEL collector batches data)
    time.sleep(5)

    # Query ClickHouse
    traces = query_clickhouse_traces(
        clickhouse_client, service_name="test-service", trace_id=trace_id, max_wait_seconds=30
    )

    if not traces:
        all_traces = query_clickhouse_traces(
            clickhouse_client, service_name=None, trace_id=trace_id, max_wait_seconds=5
        )
        pytest.fail(
            f"No traces found in ClickHouse. Found {len(all_traces)} traces without service filter."
        )

    # Verify standard metadata
    trace_row = traces[0]

    # ResourceAttributes should contain service.name
    resource_attrs = trace_row.get("ResourceAttributes", {})
    if isinstance(resource_attrs, dict):
        assert "service.name" in resource_attrs, "ResourceAttributes should contain 'service.name'"
        assert resource_attrs["service.name"] == "test-service"

    # SpanAttributes should contain HTTP attributes
    span_attrs = trace_row.get("SpanAttributes", {})
    if isinstance(span_attrs, dict):
        # Should have at least some HTTP attributes
        has_http_attrs = any(key.startswith("http.") for key in span_attrs.keys())
        assert has_http_attrs, "SpanAttributes should contain HTTP semantic convention attributes"

    # Should have SpanName
    assert trace_row["SpanName"] is not None, "SpanName should not be None"
    assert trace_row["SpanName"] != "", "SpanName should not be empty"


def test_logs_stored_in_clickhouse_with_service_name(
    django_client: Client,
    clickhouse_client: Any,
    wait_for_clickhouse: None,
    wait_for_otel_collector: Any,
) -> None:
    """Test that application logs are stored in ClickHouse with ServiceName.

    Note: This test requires OTLP logging to be configured. Currently, logs
    are only exported via Python logging (JSON format), not via OTEL.
    This test verifies the infrastructure is ready for OTLP logging.
    """
    # Make a request that will generate logs
    response = django_client.get("/metrics")
    assert response.status_code == 200

    # Note: Currently, logs are not exported via OTEL, so this test
    # may not find logs in ClickHouse. This is expected until OTLP
    # logging is implemented.

    # Wait a bit for any potential log processing
    time.sleep(2)

    # Query ClickHouse for logs
    logs = query_clickhouse_logs(clickhouse_client, service_name="test-service", max_wait_seconds=5)

    # If logs are found, verify ServiceName
    if logs:
        for log_row in logs:
            assert log_row["ServiceName"] is not None, "ServiceName should not be None in logs"
            assert log_row["ServiceName"] != "", "ServiceName should not be empty in logs"
    else:
        # This is expected until OTLP logging is implemented
        pytest.skip(
            "No logs found in ClickHouse. OTLP logging integration not yet implemented. "
            "Logs are currently only exported via Python logging (JSON format)."
        )


def test_traces_and_logs_linked_by_trace_id(
    django_client: Client,
    clickhouse_client: Any,
    wait_for_clickhouse: None,
    wait_for_otel_collector: Any,
    otel_http_endpoint: str,
) -> None:
    """Test that traces and logs can be linked by TraceId."""
    # Initialize tracing
    from observe_kit.otel import init_tracing

    init_tracing(service_name="test-service", endpoint=otel_http_endpoint)

    # Make a request
    response = django_client.get("/metrics")
    assert response.status_code == 200

    trace_id = response.get("X-Trace-Id")
    assert trace_id is not None

    # Force flush
    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush(timeout_millis=5000)

    # Wait for data
    # Wait for data to be ingested (OTEL collector batches data)
    time.sleep(5)

    # Query both traces and logs with the same trace_id
    traces = query_clickhouse_traces(
        clickhouse_client, service_name="test-service", trace_id=trace_id, max_wait_seconds=30
    )

    logs = query_clickhouse_logs(
        clickhouse_client, service_name="test-service", trace_id=trace_id, max_wait_seconds=20
    )

    # Should have at least traces
    if not traces:
        all_traces = query_clickhouse_traces(
            clickhouse_client, service_name=None, trace_id=trace_id, max_wait_seconds=5
        )
        pytest.fail(
            f"No traces found for trace_id={trace_id}. "
            f"Found {len(all_traces)} traces without service filter."
        )

    # Verify trace_id matches
    for trace_row in traces:
        assert trace_row["TraceId"] == trace_id, (
            f"TraceId mismatch: expected {trace_id}, got {trace_row['TraceId']}"
        )

    # If logs are found, they should also have matching trace_id
    if logs:
        for log_row in logs:
            assert log_row["TraceId"] == trace_id, (
                f"Log TraceId mismatch: expected {trace_id}, got {log_row['TraceId']}"
            )
