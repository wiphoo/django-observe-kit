"""Edge case tests for OTEL configuration."""

from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.skipif(
    not __import__("importlib.util").util.find_spec("django"), reason="django not installed"
)


def test_validate_service_name_empty() -> None:
    """Test _validate_service_name with empty string."""
    from observe_kit.otel.config import ConfigurationError, _validate_service_name

    with pytest.raises(ConfigurationError, match="must be a non-empty string"):
        _validate_service_name("")


def test_validate_service_name_too_long() -> None:
    """Test _validate_service_name with too long name."""
    from observe_kit.otel.config import ConfigurationError, _validate_service_name

    long_name = "a" * 256
    with pytest.raises(ConfigurationError, match="255 characters or less"):
        _validate_service_name(long_name)


def test_validate_service_name_invalid_chars() -> None:
    """Test _validate_service_name with invalid characters."""
    from observe_kit.otel.config import ConfigurationError, _validate_service_name

    with pytest.raises(ConfigurationError, match="alphanumeric"):
        _validate_service_name("service@name")


def test_validate_endpoint_no_scheme() -> None:
    """Test _validate_endpoint with URL without scheme."""
    from observe_kit.otel.config import ConfigurationError, _validate_endpoint

    with pytest.raises(ConfigurationError, match="scheme"):
        _validate_endpoint("localhost:4317")


def test_validate_endpoint_invalid_scheme() -> None:
    """Test _validate_endpoint with invalid scheme."""
    from observe_kit.otel.config import ConfigurationError, _validate_endpoint

    with pytest.raises(ConfigurationError, match="http or https"):
        _validate_endpoint("ftp://localhost:4317")


def test_validate_resource_attributes_invalid_key() -> None:
    """Test _validate_resource_attributes with invalid key type."""
    from observe_kit.otel.config import ConfigurationError, _validate_resource_attributes

    with pytest.raises(ConfigurationError, match="keys must be strings"):
        _validate_resource_attributes({123: "value"})


def test_validate_resource_attributes_invalid_value() -> None:
    """Test _validate_resource_attributes with invalid value type."""
    from observe_kit.otel.config import ConfigurationError, _validate_resource_attributes

    with pytest.raises(ConfigurationError, match="values must be strings"):
        _validate_resource_attributes({"key": 123})


def test_span_namer_name_for_request_with_route() -> None:
    """Test SpanNamer.name_for_request when context has route."""
    from django.test import RequestFactory

    from observe_kit.context import RequestContext, reset_request_context, set_request_context
    from observe_kit.otel.config import SpanNamer

    reset_request_context()
    context = RequestContext()
    context.route = "test-route"
    set_request_context(context)

    namer = SpanNamer()
    request = RequestFactory().get("/test")
    result = namer.name_for_request(request)

    assert result == "test-route"


def test_span_namer_name_for_request_with_resolver_route() -> None:
    """Test SpanNamer.name_for_request with resolver_match.route."""
    from django.test import RequestFactory

    from observe_kit.context import reset_request_context
    from observe_kit.otel.config import SpanNamer

    reset_request_context()

    namer = SpanNamer()
    request = RequestFactory().get("/test")
    mock_resolver = type("MockResolver", (), {"route": "/api/test"})()
    request.resolver_match = mock_resolver

    result = namer.name_for_request(request)
    assert result == "/api/test"


def test_span_namer_name_for_request_with_view_name() -> None:
    """Test SpanNamer.name_for_request with resolver_match.view_name."""
    from django.test import RequestFactory

    from observe_kit.context import reset_request_context
    from observe_kit.otel.config import SpanNamer

    reset_request_context()

    namer = SpanNamer()
    request = RequestFactory().get("/test")
    mock_resolver = type("MockResolver", (), {"view_name": "test_view"})()
    request.resolver_match = mock_resolver

    result = namer.name_for_request(request)
    assert result == "test_view"


def test_span_namer_update_span_name() -> None:
    """Test SpanNamer.update_span_name."""
    from django.test import RequestFactory

    from observe_kit.context import RequestContext, reset_request_context, set_request_context
    from observe_kit.otel.config import SpanNamer

    reset_request_context()
    context = RequestContext()
    context.route = "updated-route"
    set_request_context(context)

    namer = SpanNamer()
    mock_span = type("MockSpan", (), {"update_name": Mock()})()
    request = RequestFactory().get("/test")

    namer.update_span_name(mock_span, request)

    mock_span.update_name.assert_called_once_with("updated-route")
