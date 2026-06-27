"""Tests for the ORM-layer immutability of ``AuditLog`` (#12).

Per-instance ``Model.save/delete`` overrides only catch single-row operations.
``QuerySet.delete()`` issues a bulk SQL DELETE in modern Django, bypassing
``Model.delete()``; ``QuerySet.update()`` is similarly unmediated. The
``AuditLogQuerySet`` + ``AuditLogManager`` block both paths.
"""

from __future__ import annotations

import pytest

from observe_kit.audit.models import AuditLog, AuditLogImmutableError, AuditLogQuerySet


def _make_record() -> AuditLog:
    return AuditLog.objects.create(action="test.create")


def test_queryset_delete_raises() -> None:
    """Bulk QuerySet delete must raise — this is the gap the model-level
    override could not cover."""
    _make_record()
    with pytest.raises(AuditLogImmutableError):
        AuditLog.objects.all().delete()


def test_queryset_filter_delete_raises() -> None:
    """``.filter(...).delete()`` was the original attack on the immutability
    claim. Must raise."""
    r = _make_record()
    with pytest.raises(AuditLogImmutableError):
        AuditLog.objects.filter(pk=r.pk).delete()


def test_queryset_update_raises() -> None:
    """``.update()`` writes via bulk SQL UPDATE bypassing ``Model.save()``.
    Must also raise."""
    _make_record()
    with pytest.raises(AuditLogImmutableError):
        AuditLog.objects.filter(action="test.create").update(action="tampered")


def test_per_instance_save_on_existing_record_still_raises() -> None:
    """Regression: the original ``Model.save`` guard must keep working."""
    r = _make_record()
    r.action = "different"
    with pytest.raises(AuditLogImmutableError):
        r.save()


def test_per_instance_delete_still_raises() -> None:
    r = _make_record()
    with pytest.raises(AuditLogImmutableError):
        r.delete()


def test_create_still_works() -> None:
    """Sanity: the immutability rules must not block initial creation."""
    r = AuditLog.objects.create(action="test.allowed")
    assert r.pk is not None


def test_manager_is_built_from_queryset() -> None:
    """The manager must surface AuditLogQuerySet's overrides so chained
    ``filter(...).delete()`` raises rather than silently passing through."""
    qs = AuditLog.objects.all()
    assert isinstance(qs, AuditLogQuerySet)
