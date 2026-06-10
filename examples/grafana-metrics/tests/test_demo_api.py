from __future__ import annotations

import pytest


@pytest.mark.django_db
def test_quote_endpoint_returns_expected_totals(client):
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
    payload = response.json()
    assert payload["quote"]["pricing"]["total"] == "51.36"


@pytest.mark.django_db
def test_metrics_endpoint_includes_request_metrics(client):
    client.post(
        "/api/quotes/quote/",
        data={
            "customer_id": "cust-metrics",
            "items": [{"sku": "sku-1", "quantity": 1, "unit_price": "10.00"}],
        },
        content_type="application/json",
    )

    response = client.get("/metrics")
    assert response.status_code == 200

    content = response.content.decode()
    assert "http_requests_total" in content
    assert "http_request_duration_seconds" in content


def test_metrics_endpoint_accepts_prometheus_scrape_host(client):
    response = client.get("/metrics", HTTP_HOST="host.docker.internal:8000")

    assert response.status_code == 200
