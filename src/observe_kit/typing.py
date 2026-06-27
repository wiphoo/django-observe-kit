from __future__ import annotations

from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

# Type aliases for Django objects (using string literals to avoid runtime dependency)
DjangoRequest: TypeAlias = "HttpRequest"
DjangoResponse: TypeAlias = "HttpResponse"

try:  # pragma: no cover - optional import
    from django.http import HttpRequest as DjangoHttpRequest
except Exception:  # pragma: no cover - fallback when Django missing
    DjangoHttpRequest = object

DjangoHttpRequestType: TypeAlias = "DjangoHttpRequest"
