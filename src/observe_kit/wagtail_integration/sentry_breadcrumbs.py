from __future__ import annotations

import importlib.util

if importlib.util.find_spec("sentry_sdk"):
    import sentry_sdk
else:  # pragma: no cover - optional dependency
    sentry_sdk = None  # type: ignore[assignment]


def add_wagtail_breadcrumb(category: str, message: str) -> None:
    """Add a Sentry breadcrumb if Sentry is installed."""

    if sentry_sdk is None:
        return
    sentry_sdk.add_breadcrumb(category=category, message=message)
