from __future__ import annotations

from django.test import override_settings

from observe_kit.metrics.prometheus import OVERFLOW_LABEL, _reset_label_guards_for_tests


def _observe_config(**overrides):
    config = {
        "SERVICE_NAME": "example-tenant-trace-security",
        "LOG_LEVEL": "INFO",
        "PII_HASH_SALT": "example-salt",
        "TRUSTED_PROXIES": ["10.0.0.1"],
        "METRICS_MAX_LABEL_CARDINALITY": 1,
    }
    config.update(overrides)
    return config


def test_tenant_id_header_is_added_to_context(client):
    response = client.get("/", HTTP_X_TENANT_ID="tenant-a")

    assert response.status_code == 200
    assert response.json()["tenant_id"] == "tenant-a"


def test_subdomain_tenant_is_added_to_context(client):
    response = client.get("/", HTTP_HOST="acme.example.test")

    assert response.status_code == 200
    assert response.json()["tenant_id"] == "acme"


def test_inbound_traceparent_is_dropped_by_default(client):
    inbound_trace_id = "11111111111111111111111111111111"
    response = client.get(
        "/",
        HTTP_TRACEPARENT=f"00-{inbound_trace_id}-2222222222222222-01",
    )

    assert response.status_code == 200
    assert response["X-Trace-Id"] != inbound_trace_id


def test_trusted_source_can_continue_inbound_trace(client):
    inbound_trace_id = "11111111111111111111111111111111"
    with override_settings(
        OBSERVE_KIT=_observe_config(
            TRUST_INCOMING_TRACE_CONTEXT=True,
            TRUSTED_TRACE_SOURCES=["203.0.113.10"],
        )
    ):
        response = client.get(
            "/",
            REMOTE_ADDR="10.0.0.1",
            HTTP_X_FORWARDED_FOR="203.0.113.10",
            HTTP_TRACEPARENT=f"00-{inbound_trace_id}-2222222222222222-01",
        )

    assert response.status_code == 200
    assert response["X-Trace-Id"] == inbound_trace_id
    assert response.json()["remote_addr"] == "203.0.113.10"


def test_tenant_label_cardinality_overflows_after_cap(client):
    _reset_label_guards_for_tests()

    client.get("/", HTTP_X_TENANT_ID="tenant-a")
    client.get("/", HTTP_X_TENANT_ID="tenant-b")
    response = client.get("/metrics")

    assert response.status_code == 200
    assert OVERFLOW_LABEL in response.content.decode()
