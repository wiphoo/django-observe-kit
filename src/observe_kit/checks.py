"""Django system checks for observe_kit settings (run by ``manage.py check``)."""

from __future__ import annotations

from typing import Any, List

from django.core.checks import CheckMessage, Error, Warning, register
from django.core.exceptions import ImproperlyConfigured


@register()  # type: ignore[untyped-decorator]
def check_metrics_auth(app_configs: Any, **kwargs: Any) -> List[CheckMessage]:
    """Report an invalid ``METRICS_AUTH`` and a token mode without a token."""
    from observe_kit.settings import get_observe_kit_settings

    try:
        cfg = get_observe_kit_settings()
    except ImproperlyConfigured as exc:
        return [
            Error(
                str(exc),
                hint="Use one of the documented METRICS_AUTH values.",
                id="observe_kit.E001",
            )
        ]
    if cfg.metrics_auth == "token" and not cfg.metrics_token:
        return [
            Warning(
                "METRICS_AUTH is 'token' but METRICS_TOKEN is empty; "
                "every /metrics request will be rejected.",
                hint="Set OBSERVE_KIT['METRICS_TOKEN'].",
                id="observe_kit.W001",
            )
        ]
    return []
