from __future__ import annotations

import hashlib

import pytest

from observe_kit.audit.models import AuditLog


@pytest.mark.django_db
def test_request_context_and_audit_payload_are_sanitized(client):
    response = client.post(
        "/api/privacy/submit/?email=alice@example.com",
        data={
            "email": "alice@example.com",
            "phone": "5551234567",
            "ssn": "123-45-6789",
            "session_id": "session-123",
        },
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["context"]["query_params"]["email"] == "a***@example.com"

    entry = AuditLog.objects.get(action="privacy_payload_submitted")
    assert entry.extra["email"] == "a***@example.com"
    assert entry.extra["phone"] == "55***"
    assert entry.extra["ssn"] == "12***"
    assert entry.extra["session_id"] == hashlib.sha256(
        b"example-saltsession-123"
    ).hexdigest()
