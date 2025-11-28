import importlib.util
import pytest

pytestmark = pytest.mark.skipif(not importlib.util.find_spec("wagtail"), reason="wagtail not installed")


def test_wagtail_hooks_module_loads():
    assert importlib.import_module("observe_kit.wagtail_integration.wagtail_hooks")
