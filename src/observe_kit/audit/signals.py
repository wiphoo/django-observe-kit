from __future__ import annotations

from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from .utils import audit


@receiver(user_logged_in)
def audit_login(sender, user, request, **kwargs):  # pragma: no cover - signal
    audit(actor=user, action="login", obj=None, request=request)


@receiver(user_logged_out)
def audit_logout(sender, user, request, **kwargs):  # pragma: no cover - signal
    audit(actor=user, action="logout", obj=None, request=request)
