"""Additional edge case tests for context."""

import pytest

pytestmark = pytest.mark.skipif(
    not __import__("importlib.util").util.find_spec("django"), reason="django not installed"
)


def test_get_request_context_with_default() -> None:
    """Test get_request_context with default parameter."""
    from observe_kit.context import (
        RequestContext,
        get_request_context,
        reset_request_context,
        set_request_context,
    )

    reset_request_context()
    custom_context = RequestContext()
    custom_context.method = "POST"
    custom_context.path = "/custom"

    # Set the context first
    set_request_context(custom_context)
    result = get_request_context(default=custom_context)
    assert result.method == "POST"
    assert result.path == "/custom"


def test_get_request_context_with_default_none() -> None:
    """Test get_request_context with default=None creates new context."""
    from observe_kit.context import RequestContext, get_request_context, reset_request_context

    reset_request_context()
    result = get_request_context(default=None)
    assert isinstance(result, RequestContext)
    assert result.method is None
    assert result.path is None
