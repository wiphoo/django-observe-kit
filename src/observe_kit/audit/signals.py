from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from .utils import audit

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser
    from django.http import HttpRequest


@receiver(user_logged_in)  # type: ignore[untyped-decorator]
def audit_login(
    sender: Any, user: AbstractUser, request: HttpRequest, **kwargs: Any
) -> None:
    audit(actor=user, action="login", obj=None, request=request)


@receiver(user_logged_out)  # type: ignore[untyped-decorator]
def audit_logout(
    sender: Any, user: AbstractUser, request: HttpRequest, **kwargs: Any
) -> None:
    audit(actor=user, action="logout", obj=None, request=request)
