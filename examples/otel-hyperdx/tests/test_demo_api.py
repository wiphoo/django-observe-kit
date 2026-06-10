from __future__ import annotations

import logging

import pytest


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
