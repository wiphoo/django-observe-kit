from __future__ import annotations

from typing import Optional

from ..context import get_request_context


def set_drf_action(route: Optional[str]) -> None:
    """Set DRF route/view name onto the shared request context."""

    context = get_request_context()
    context.route = route or context.route
