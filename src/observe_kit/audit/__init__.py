"""Audit logging module for observe_kit.

This module provides audit logging functionality. Models are imported lazily
to avoid circular dependencies during Django setup.
"""

from .utils import audit

__all__ = ["AuditLog", "ObserveAuditConfig", "audit"]


def __getattr__(name: str):
    """Lazy import for models to avoid circular dependencies during Django setup."""
    if name == "AuditLog":
        from .models import AuditLog

        return AuditLog
    if name == "ObserveAuditConfig":
        from .models import ObserveAuditConfig

        return ObserveAuditConfig
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
