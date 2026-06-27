"""Tests for audit signal handlers."""

from unittest.mock import Mock, patch

import pytest
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.test import RequestFactory

pytestmark = pytest.mark.skipif(
    not __import__("importlib.util").util.find_spec("django"), reason="django not installed"
)


@pytest.fixture
def request_factory() -> RequestFactory:
    """Create a request factory."""
    from django.test import RequestFactory

    return RequestFactory()


@pytest.fixture
def mock_user() -> Mock:
    """Create a mock user."""
    user = Mock()
    user.id = 1
    user.username = "testuser"
    return user


def test_audit_login_signal(request_factory: RequestFactory, mock_user: Mock) -> None:
    """Test that login signal triggers audit."""
    request = request_factory.get("/")
    request.user = mock_user

    with patch("observe_kit.audit.signals.audit") as mock_audit:
        # Send the signal
        user_logged_in.send(sender=type(mock_user), user=mock_user, request=request)

        # Verify audit was called
        mock_audit.assert_called_once_with(
            actor=mock_user, action="login", obj=None, request=request
        )


def test_audit_logout_signal(request_factory: RequestFactory, mock_user: Mock) -> None:
    """Test that logout signal triggers audit."""
    request = request_factory.get("/")
    request.user = mock_user

    with patch("observe_kit.audit.signals.audit") as mock_audit:
        # Send the signal
        user_logged_out.send(sender=type(mock_user), user=mock_user, request=request)

        # Verify audit was called
        mock_audit.assert_called_once_with(
            actor=mock_user, action="logout", obj=None, request=request
        )
