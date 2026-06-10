from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings

from observe_kit.metrics.prometheus import _reset_unauth_warning_for_tests


def _observe_config(**overrides):
    config = {
        "SERVICE_NAME": "example-metrics-security",
        "LOG_LEVEL": "INFO",
        "PII_HASH_SALT": "example-salt",
    }
    config.update(overrides)
    return config


def test_token_metrics_auth_rejects_missing_token(client):
    with override_settings(OBSERVE_KIT=_observe_config(METRICS_AUTH="token", METRICS_TOKEN="s3cret")):
        response = client.get("/metrics")

    assert response.status_code == 401


def test_token_metrics_auth_accepts_bearer_token(client):
    with override_settings(OBSERVE_KIT=_observe_config(METRICS_AUTH="token", METRICS_TOKEN="s3cret")):
        response = client.get("/metrics", HTTP_AUTHORIZATION="Bearer s3cret")

    assert response.status_code == 200


@pytest.mark.django_db
def test_staff_metrics_auth_requires_staff_user(client):
    User = get_user_model()
    staff = User.objects.create_user(username="staff", password="pw", is_staff=True)
    regular = User.objects.create_user(username="regular", password="pw", is_staff=False)

    with override_settings(OBSERVE_KIT=_observe_config(METRICS_AUTH="staff")):
        client.force_login(regular)
        assert client.get("/metrics").status_code == 403
        client.force_login(staff)
        assert client.get("/metrics").status_code == 200


def test_unauthenticated_metrics_warns_when_debug_false(client):
    _reset_unauth_warning_for_tests()

    with override_settings(DEBUG=False, OBSERVE_KIT=_observe_config(METRICS_AUTH="none")):
        with pytest.warns(RuntimeWarning, match="/metrics endpoint is exposed"):
            response = client.get("/metrics")

    assert response.status_code == 200
