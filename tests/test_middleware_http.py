import importlib.util
import pytest

pytestmark = pytest.mark.skipif(not importlib.util.find_spec("django"), reason="django not installed")


def test_placeholder_for_middleware_imports():
    assert importlib.util.find_spec("observe_kit.context_middleware") is not None
