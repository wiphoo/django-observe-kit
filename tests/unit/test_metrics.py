import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    not importlib.util.find_spec("prometheus_client"), reason="prometheus not installed"
)


def test_metrics_exports() -> None:
    mod = importlib.import_module("observe_kit.metrics")
    assert hasattr(mod, "HTTP_REQUESTS_TOTAL")
