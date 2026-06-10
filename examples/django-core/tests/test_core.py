from __future__ import annotations

import logging

import pytest


@pytest.mark.django_db
def test_plain_django_request_gets_trace_header_and_context(client, caplog):
    caplog.set_level(logging.INFO)

    response = client.get("/")

    assert response.status_code == 200
    assert response["X-Trace-Id"]
    payload = response.json()
    assert payload["observability"]["trace_id"] == response["X-Trace-Id"]
    assert "core_home_view" in caplog.text


@pytest.mark.django_db
def test_metrics_endpoint_records_plain_django_request(client):
    client.get("/")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert "http_requests_total" in response.content.decode()
