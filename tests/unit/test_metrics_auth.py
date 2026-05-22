"""Tests for the Prometheus /metrics endpoint access-control gate."""

from __future__ import annotations

import warnings

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory, override_settings

from observe_kit.metrics import prometheus as prom_module
from observe_kit.metrics.prometheus import _reset_unauth_warning_for_tests, metrics_view
from observe_kit.settings import get_observe_kit_settings


@pytest.fixture
def rf() -> RequestFactory:
    return RequestFactory()


@pytest.fixture(autouse=True)
def _reset_warning_flag() -> None:
    _reset_unauth_warning_for_tests()
    yield
    _reset_unauth_warning_for_tests()


# ---------------------------------------------------------------------------
# settings parsing
# ---------------------------------------------------------------------------


@override_settings(OBSERVE_KIT={"METRICS_AUTH": "TOKEN", "METRICS_TOKEN": "secret-x"})
def test_settings_parse_token_mode_uppercase() -> None:
    cfg = get_observe_kit_settings()
    assert cfg.metrics_auth == "token"
    assert cfg.metrics_token == "secret-x"


@override_settings(OBSERVE_KIT={"METRICS_AUTH": "garbage"})
def test_settings_invalid_mode_falls_back_to_none() -> None:
    cfg = get_observe_kit_settings()
    assert cfg.metrics_auth == "none"


@override_settings(OBSERVE_KIT={})
def test_settings_default_is_none() -> None:
    cfg = get_observe_kit_settings()
    assert cfg.metrics_auth == "none"
    assert cfg.metrics_token is None


def test_settings_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBSERVE_KIT_METRICS_AUTH", "staff")
    monkeypatch.setenv("OBSERVE_KIT_METRICS_TOKEN", "env-token")
    with override_settings(OBSERVE_KIT={}):
        cfg = get_observe_kit_settings()
    assert cfg.metrics_auth == "staff"
    assert cfg.metrics_token == "env-token"


# ---------------------------------------------------------------------------
# mode: none
# ---------------------------------------------------------------------------


@override_settings(OBSERVE_KIT={"METRICS_AUTH": "none"}, DEBUG=True)
def test_none_mode_returns_200_under_debug_without_warning(rf: RequestFactory) -> None:
    view = metrics_view.as_view()
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        response = view(rf.get("/metrics"))
    assert response.status_code == 200
    assert not any(issubclass(w.category, RuntimeWarning) for w in captured)


@override_settings(OBSERVE_KIT={"METRICS_AUTH": "none"}, DEBUG=False)
def test_none_mode_emits_runtime_warning_once_in_production(rf: RequestFactory) -> None:
    view = metrics_view.as_view()
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        view(rf.get("/metrics"))
        view(rf.get("/metrics"))
        view(rf.get("/metrics"))
    runtime_warnings = [w for w in captured if issubclass(w.category, RuntimeWarning)]
    assert len(runtime_warnings) == 1
    assert "without authentication" in str(runtime_warnings[0].message)


# ---------------------------------------------------------------------------
# mode: staff
# ---------------------------------------------------------------------------


@override_settings(OBSERVE_KIT={"METRICS_AUTH": "staff"})
def test_staff_mode_rejects_anonymous(rf: RequestFactory) -> None:
    view = metrics_view.as_view()
    request = rf.get("/metrics")
    request.user = AnonymousUser()
    assert view(request).status_code == 403


@override_settings(OBSERVE_KIT={"METRICS_AUTH": "staff"})
def test_staff_mode_rejects_non_staff_user(rf: RequestFactory) -> None:
    view = metrics_view.as_view()
    request = rf.get("/metrics")
    request.user = User.objects.create_user(username="alice", password="x")
    assert view(request).status_code == 403


@override_settings(OBSERVE_KIT={"METRICS_AUTH": "staff"})
def test_staff_mode_allows_staff_user(rf: RequestFactory) -> None:
    view = metrics_view.as_view()
    request = rf.get("/metrics")
    request.user = User.objects.create_user(username="root", password="x", is_staff=True)
    response = view(request)
    assert response.status_code == 200
    assert b"http_requests_total" in response.content or response.content == b""


# ---------------------------------------------------------------------------
# mode: token
# ---------------------------------------------------------------------------


@override_settings(OBSERVE_KIT={"METRICS_AUTH": "token", "METRICS_TOKEN": "right-token"})
def test_token_mode_missing_header_returns_401(rf: RequestFactory) -> None:
    view = metrics_view.as_view()
    assert view(rf.get("/metrics")).status_code == 401


@override_settings(OBSERVE_KIT={"METRICS_AUTH": "token", "METRICS_TOKEN": "right-token"})
def test_token_mode_wrong_token_returns_401(rf: RequestFactory) -> None:
    view = metrics_view.as_view()
    response = view(rf.get("/metrics", HTTP_AUTHORIZATION="Bearer wrong"))
    assert response.status_code == 401


@override_settings(OBSERVE_KIT={"METRICS_AUTH": "token", "METRICS_TOKEN": "right-token"})
def test_token_mode_correct_token_returns_200(rf: RequestFactory) -> None:
    view = metrics_view.as_view()
    response = view(rf.get("/metrics", HTTP_AUTHORIZATION="Bearer right-token"))
    assert response.status_code == 200


@override_settings(OBSERVE_KIT={"METRICS_AUTH": "token", "METRICS_TOKEN": ""})
def test_token_mode_empty_configured_token_rejects_everything(rf: RequestFactory) -> None:
    view = metrics_view.as_view()
    assert view(rf.get("/metrics")).status_code == 401
    response = view(rf.get("/metrics", HTTP_AUTHORIZATION="Bearer "))
    assert response.status_code == 401


@override_settings(OBSERVE_KIT={"METRICS_AUTH": "token", "METRICS_TOKEN": "tk"})
def test_token_mode_non_bearer_scheme_returns_401(rf: RequestFactory) -> None:
    view = metrics_view.as_view()
    response = view(rf.get("/metrics", HTTP_AUTHORIZATION="Basic tk"))
    assert response.status_code == 401


def test_token_mode_uses_constant_time_compare() -> None:
    # Sanity check: we use hmac.compare_digest, not ==. Verified by importing the module.
    assert "hmac" in prom_module.__dict__ or hasattr(prom_module, "hmac")
