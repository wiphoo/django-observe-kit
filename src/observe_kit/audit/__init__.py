"""Audit logging module for observe_kit.

This module provides audit logging functionality. Models are imported lazily
to avoid circular dependencies during Django setup.
"""

from typing import Any

from .apps import ObserveAuditConfig
from .utils import audit

__all__ = ["AuditLog", "AuditLogImmutableError", "ObserveAuditConfig", "audit"]


def __getattr__(name: str) -> Any:
    """Lazy import for models to avoid circular dependencies during Django setup."""
    if name == "AuditLog":
        from .models import AuditLog

        return AuditLog
    if name == "AuditLogImmutableError":
        from .models import AuditLogImmutableError

        return AuditLogImmutableError
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
