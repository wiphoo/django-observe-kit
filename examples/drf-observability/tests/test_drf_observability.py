from __future__ import annotations

import pytest


@pytest.mark.django_db
def test_drf_viewset_action_is_reported_as_route(client):
    response = client.post(
        "/api/quotes/quote/",
        data={
            "customer_id": "cust-drf",
            "items": [{"sku": "sku-1", "quantity": 1, "unit_price": "10.00"}],
        },
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response["X-Trace-Id"]
    payload = response.json()
    assert payload["observability"]["trace_id"] == response["X-Trace-Id"]
    assert payload["observability"]["route"] == "drf.QuoteViewSet.quote"


@pytest.mark.django_db
def test_drf_exception_handler_captures_server_error(client, monkeypatch):
    captured = []

    def fake_capture_exception(error):
        captured.append(str(error))

    monkeypatch.setattr("sentry_sdk.capture_exception", fake_capture_exception)

    response = client.get("/api/quotes/failure/")

    assert response.status_code == 500
    assert response.json()["detail"] == "Server Error"
    assert captured == ["Intentional DRF observability demo failure"]
