from __future__ import annotations

from typing import TypeAlias

try:  # pragma: no cover - optional import
    from django.http import HttpRequest as DjangoHttpRequest
except Exception:  # pragma: no cover - fallback when Django missing
    DjangoHttpRequest = object  # type: ignore[misc]

HttpRequest: TypeAlias = "DjangoHttpRequest"
