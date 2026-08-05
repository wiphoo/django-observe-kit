from observe_kit.pii_rules import PiiLevel, sanitize_body, sanitize_headers, sanitize_query_params


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


def test_sanitize_body_scrubs_pair_list_by_key() -> None:
    # A list-of-[key, value] pairs anywhere in a body is scrubbed by key (dict
    # semantics), not positionally — so secrets under known keys don't leak.
    cleaned = sanitize_body(
        {"form": [["authorization", "Bearer x"], ["phone", "0812345678"]]}, PiiLevel.BASIC
    )
    form = cleaned["form"]
    assert [k for k, _ in form] == ["phone"]  # authorization dropped
    assert form == [["phone", "08***"]]


def test_sanitize_body_leaves_positional_lists_positional() -> None:
    # A positional array / numeric matrix must not be treated as pairs.
    assert sanitize_body([{"phone": "0812345678"}, "note"], PiiLevel.BASIC) == [
        {"phone": "08***"},
        "note",
    ]
    assert sanitize_body([[1, 2], [3, 4]], PiiLevel.BASIC) == [[1, 2], [3, 4]]


def test_sanitize_query_aliases_match_either_name() -> None:
    # A field keyed by its column name still matches a rule/default keyed by the
    # semantic alias, and an operator rule keyed by the column name matches too.
    hashed = sanitize_query_params(
        {"remote_addr": "1.2.3.4"}, PiiLevel.SENSITIVE, aliases={"remote_addr": "ip"}
    )
    # Default IP hashing fires via the ``ip`` alias even though the key is the
    # column name.
    assert hashed["remote_addr"] != "1.2.3.4" and len(hashed["remote_addr"]) == 64

    dropped = sanitize_query_params(
        {"remote_addr": "1.2.3.4"},
        PiiLevel.BASIC,
        extra_drop=frozenset({"remote_addr"}),
        aliases={"remote_addr": "ip"},
    )
    # Operator rule keyed on the column name wins over the alias default.
    assert "remote_addr" not in dropped


def test_sanitize_query_alias_lookup_is_case_insensitive() -> None:
    # Regression: the alias was looked up by the raw mapping key while the key
    # set was lower-cased, so a differently-cased mapping key (``Remote_Addr``)
    # missed its ``remote_addr`` alias and left the client IP stored raw.
    hashed = sanitize_query_params(
        {"Remote_Addr": "1.2.3.4"}, PiiLevel.SENSITIVE, aliases={"remote_addr": "ip"}
    )
    assert hashed["Remote_Addr"] != "1.2.3.4" and len(hashed["Remote_Addr"]) == 64
