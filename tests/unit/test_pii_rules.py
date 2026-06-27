from observe_kit.pii_rules import PiiLevel, sanitize_headers, sanitize_query_params


def test_sanitize_headers_masks_and_drops() -> None:
    headers = {"Authorization": "secret", "Email": "user@example.com", "X-Id": "123"}
    cleaned = sanitize_headers(headers, PiiLevel.BASIC)
    assert "Authorization" not in cleaned
    assert cleaned["Email"].startswith("u***@example.com")
    assert cleaned["X-Id"] == "123"


def test_sanitize_query_hashes_sensitive() -> None:
    params = {"ip": "1.2.3.4"}
    cleaned = sanitize_query_params(params, PiiLevel.SENSITIVE)
    assert cleaned["ip"] != "1.2.3.4"
