from __future__ import annotations

import logging

import pytest

from observe_kit.audit.models import AuditLog
from demo_api.services import build_quote
from observe_kit.context import RequestContext, reset_request_context, set_request_context


@pytest.mark.django_db
def test_quote_endpoint_returns_trace_metadata(client, caplog):
    caplog.set_level(logging.INFO)

    response = client.post(
        "/api/quotes/quote/",
        data={
            "customer_id": "cust-123",
            "items": [
                {"sku": "sku-observe-kit", "quantity": 2, "unit_price": "19.50"},
                {"sku": "sku-drf", "quantity": 1, "unit_price": "9.00"},
            ],
        },
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response["X-Trace-Id"]

    payload = response.json()
    assert payload["observability"]["trace_id"] == response["X-Trace-Id"]
    assert payload["observability"]["route"] == "drf.QuoteViewSet.quote"
    assert payload["quote"]["pricing"]["total"] == "51.36"
    assert "quote_requested" in caplog.text
    assert "quote_completed" in caplog.text
    audit_entry = AuditLog.objects.get(action="quote_generated")
    assert audit_entry.extra["customer_id"] == "cust-123"
    assert audit_entry.trace_id == response["X-Trace-Id"]


@pytest.mark.django_db
def test_audit_endpoint_returns_recent_entries(client):
    client.post(
        "/api/quotes/quote/",
        data={
            "customer_id": "cust-audit",
            "items": [{"sku": "sku-1", "quantity": 1, "unit_price": "10.00"}],
        },
        content_type="application/json",
    )

    response = client.get("/api/audit/")
    assert response.status_code == 200

    payload = response.json()
    assert payload[0]["action"] == "quote_generated"
    assert payload[0]["extra"]["customer_id"] == "cust-audit"


@pytest.mark.django_db
def test_build_quote_creates_expected_totals():
    reset_request_context()
    set_request_context(
        RequestContext(route="drf.QuoteViewSet.quote", trace_id="abc123", span_id="def456")
    )

    result = build_quote(
        customer_id="cust-456",
        items=[
            {"sku": "sku-1", "quantity": 2, "unit_price": "15.00"},
            {"sku": "sku-2", "quantity": 1, "unit_price": "10.00"},
        ],
        request=None,
    )

    assert result["inventory"]["available"] is True
    assert result["pricing"]["subtotal"] == "40.00"
    assert result["pricing"]["tax"] == "2.80"
    assert result["pricing"]["total"] == "42.80"
    assert AuditLog.objects.filter(action="quote_generated", trace_id="abc123").exists()
