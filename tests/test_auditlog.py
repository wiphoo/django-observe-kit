import importlib.util
import pytest

pytestmark = pytest.mark.skipif(not importlib.util.find_spec("django"), reason="django not installed")


def test_audit_module_importable():
    assert importlib.import_module("observe_kit.audit")
