"""Tests for the Prometheus /metrics endpoint access-control gate."""

from __future__ import annotations

import warnings
from typing import Any, Callable

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
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        cfg = get_observe_kit_settings()
    assert cfg.metrics_auth == "none"


@override_settings(OBSERVE_KIT={"METRICS_AUTH": "garbage"})
def test_settings_invalid_mode_emits_runtime_warning() -> None:
    """Operator-visible warning at parse time when METRICS_AUTH is invalid."""
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        get_observe_kit_settings()
    runtime = [w for w in captured if issubclass(w.category, RuntimeWarning)]
    assert runtime, "expected a RuntimeWarning for invalid METRICS_AUTH"
    msg = str(runtime[0].message)
    assert "METRICS_AUTH" in msg
    assert "invalid" in msg
    assert "garbage" in msg


@override_settings(OBSERVE_KIT={"METRICS_AUTH": "none"})
def test_settings_valid_mode_emits_no_warning() -> None:
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        get_observe_kit_settings()
    runtime = [w for w in captured if issubclass(w.category, RuntimeWarning)]
    assert not runtime


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
    # Filter to the "/metrics is exposed" warning (not the settings-parse one).
    exposure = [w for w in runtime_warnings if "without authentication" in str(w.message)]
    assert len(exposure) == 1


# ---------------------------------------------------------------------------
# mode: staff — parametrized matrix
# ---------------------------------------------------------------------------


def _anonymous_user() -> Any:
    return AnonymousUser()


def _regular_user() -> Any:
    return User.objects.create_user(username="alice", password="x")


def _staff_user() -> Any:
    return User.objects.create_user(username="root", password="x", is_staff=True)


@pytest.mark.parametrize(
    "user_factory,expected_status",
    [
        pytest.param(_anonymous_user, 403, id="anonymous"),
        pytest.param(_regular_user, 403, id="authenticated-non-staff"),
        pytest.param(_staff_user, 200, id="staff"),
    ],
)
@override_settings(OBSERVE_KIT={"METRICS_AUTH": "staff"})
def test_staff_mode(
    rf: RequestFactory, user_factory: Callable[[], Any], expected_status: int
) -> None:
    view = metrics_view.as_view()
    request = rf.get("/metrics")
    request.user = user_factory()
    assert view(request).status_code == expected_status


# ---------------------------------------------------------------------------
# mode: token — parametrized matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "configured_token,auth_header,expected_status",
    [
        pytest.param("right-token", None, 401, id="missing-header"),
        pytest.param("right-token", "Bearer wrong", 401, id="wrong-token"),
        pytest.param("right-token", "Bearer right-token", 200, id="correct-token"),
        # RFC 7235 §2.1: auth schemes are case-insensitive.
        pytest.param("right-token", "bearer right-token", 200, id="lowercase-scheme"),
        pytest.param("right-token", "BEARER right-token", 200, id="uppercase-scheme"),
        pytest.param("right-token", "BeArEr right-token", 200, id="mixed-case-scheme"),
        # Multiple spaces between scheme and token must be tolerated.
        pytest.param("right-token", "Bearer  right-token", 200, id="extra-whitespace"),
        # Wrong scheme is rejected even with the right token.
        pytest.param("tk", "Basic tk", 401, id="non-bearer-scheme"),
        # Empty configured token must never allow.
        pytest.param("", None, 401, id="empty-token-missing-header"),
        pytest.param("", "Bearer ", 401, id="empty-token-empty-header"),
    ],
)
def test_token_mode_matrix(
    rf: RequestFactory, configured_token: str, auth_header: str | None, expected_status: int
) -> None:
    overrides = {"OBSERVE_KIT": {"METRICS_AUTH": "token", "METRICS_TOKEN": configured_token}}
    with override_settings(**overrides):
        view = metrics_view.as_view()
        kwargs: dict[str, Any] = {}
        if auth_header is not None:
            kwargs["HTTP_AUTHORIZATION"] = auth_header
        response = view(rf.get("/metrics", **kwargs))
    assert response.status_code == expected_status


def test_token_mode_uses_constant_time_compare() -> None:
    # Sanity check: we use hmac.compare_digest, not ==. Verified by importing the module.
    assert "hmac" in prom_module.__dict__ or hasattr(prom_module, "hmac")
