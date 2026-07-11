from __future__ import annotations

import pytest


@pytest.mark.django_db
def test_failure_endpoint_returns_500(client):
    response = client.get("/api/demo/failure/")
    assert response.status_code == 500
    assert response.json()["detail"] == "Server Error"


@pytest.mark.django_db
def test_failure_endpoint_calls_sentry_capture(client, monkeypatch):
    captured = []

    def fake_capture_exception(error):
        captured.append(str(error))

    monkeypatch.setattr("demo_api.views.sentry_sdk.capture_exception", fake_capture_exception)

    response = client.get("/api/demo/failure/")

    assert response.status_code == 500
    assert captured == ["Intentional Sentry demo failure"]
