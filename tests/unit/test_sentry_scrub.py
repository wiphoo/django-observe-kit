"""Tests for observe_kit.sentry.config.scrub_event PII coverage (issue #22).

scrub_event must apply the per-sink PII level to every event field that can
carry PII — not just request headers.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    not __import__("importlib.util").util.find_spec("django"), reason="django not installed"
)


def _scrub(event, level, **kwargs):
    from observe_kit.pii_rules import PiiLevel
    from observe_kit.sentry.config import scrub_event

    return scrub_event(event, None, getattr(PiiLevel, level), hash_salt="pepper", **kwargs)


def test_scrubs_request_headers() -> None:
    """Regression: the original header scrubbing still works."""
    event = {
        "request": {"headers": {"Authorization": "Bearer secret", "Accept": "application/json"}}
    }
    result = _scrub(event, "BASIC")
    headers = result["request"]["headers"]
    assert "Authorization" not in headers  # dropped
    assert headers["Accept"] == "application/json"


def test_scrubs_list_of_tuple_headers() -> None:
    # Sentry's documented list-of-pairs header form must be sanitized too.
    event = {
        "request": {"headers": [["Authorization", "Bearer secret"], ["Accept", "application/json"]]}
    }
    result = _scrub(event, "BASIC")
    headers = result["request"]["headers"]
    keys = [k for k, _ in headers]
    assert "Authorization" not in keys  # dropped
    assert ["Accept", "application/json"] in headers


def test_scrubs_standard_span_url_and_query_keys() -> None:
    event = {
        "spans": [
            {
                "op": "http.client",
                "data": {
                    "http.url": "https://api.test/u?email=alice@example.com",
                    "url.full": "https://api.test/v?email=bob@example.com",
                    "http.query": "email=carol@example.com&page=2",
                    "url.query": "?email=dave@example.com",
                },
            }
        ]
    }
    result = _scrub(event, "BASIC")
    data = result["spans"][0]["data"]
    assert "alice@example.com" not in data["http.url"]
    assert "bob@example.com" not in data["url.full"]
    assert "carol@example.com" not in data["http.query"] and "page=2" in data["http.query"]
    assert "dave@example.com" not in data["url.query"]


def test_masks_query_string_field() -> None:
    event = {"request": {"query_string": "email=alice@example.com&page=2"}}
    result = _scrub(event, "BASIC")
    qs = result["request"]["query_string"]
    assert "alice%40example.com" not in qs and "alice@example.com" not in qs
    assert "page=2" in qs


def test_query_string_field_honors_explicit_drop() -> None:
    # EXTRA_DROP_HEADERS={"query_string"} must drop the whole field, not just
    # scrub known parameter names (a non-email token would otherwise survive).
    event = {"request": {"query_string": "token=supersecret&page=2"}}
    result = _scrub(event, "BASIC", extra_drop=frozenset({"query_string"}))
    assert "query_string" not in result["request"]


def test_query_string_field_honors_explicit_mask() -> None:
    event = {"request": {"query_string": "token=supersecret&page=2"}}
    result = _scrub(event, "BASIC", extra_mask=frozenset({"query_string"}))
    qs = result["request"]["query_string"]
    assert qs == "[Filtered]"


def test_query_string_field_mask_applies_to_structured_forms() -> None:
    # EXTRA_MASK_FIELDS={"query_string"} must mask the whole field even in the
    # dict / list-of-pairs shapes (a non-email token under an unknown param
    # would otherwise survive per-parameter scrubbing).
    dict_evt = _scrub(
        {"request": {"query_string": {"token": "supersecret", "page": "2"}}},
        "BASIC",
        extra_mask=frozenset({"query_string"}),
    )
    assert dict_evt["request"]["query_string"] == "[Filtered]"
    list_evt = _scrub(
        {"request": {"query_string": [["token", "supersecret"]]}},
        "BASIC",
        extra_mask=frozenset({"query_string"}),
    )
    assert list_evt["request"]["query_string"] == "[Filtered]"


def test_query_string_field_hash_applies_to_structured_forms() -> None:
    event = {"request": {"query_string": {"token": "supersecret"}}}
    result = _scrub(event, "SENSITIVE", extra_hash=frozenset({"query_string"}))
    qs = result["request"]["query_string"]
    assert "supersecret" not in str(qs) and len(qs) == 64  # sha256 hex


def test_query_string_dict_no_rule_keeps_per_param_scrub() -> None:
    # Regression: without a field-level rule, structured forms still get the
    # per-parameter rules (email masked) and keep their dict shape.
    event = {"request": {"query_string": {"email": "bob@example.com", "q": "widgets"}}}
    result = _scrub(event, "BASIC")
    qs = result["request"]["query_string"]
    assert qs["email"] == "b***@example.com" and qs["q"] == "widgets"


def test_masks_email_in_query_value_under_nonsensitive_key() -> None:
    # q= is not a MASK_FIELD, but the email value must still be masked.
    event = {"request": {"query_string": "q=alice@example.com&page=2"}}
    result = _scrub(event, "BASIC")
    qs = result["request"]["query_string"]
    assert "alice@example.com" not in qs and "alice%40example.com" not in qs
    assert "page=2" in qs


def test_masks_email_in_query_value_dict_and_list_forms() -> None:
    dict_evt = _scrub({"request": {"query_string": {"q": "bob@example.com"}}}, "BASIC")
    assert "bob@example.com" not in dict_evt["request"]["query_string"]["q"]
    list_evt = _scrub({"request": {"query_string": [["q", "carol@example.com"]]}}, "BASIC")
    assert "carol@example.com" not in dict(list_evt["request"]["query_string"])["q"]


def test_scrubs_nested_url_in_query_value() -> None:
    # A next/redirect parameter whose value is itself a URL (or path with its own
    # query) must have its inner parameters scrubbed too — not re-emitted raw.
    url_evt = _scrub({"request": {"url": "/login?next=/search%3Fphone%3D0812345678"}}, "BASIC")
    assert "0812345678" not in url_evt["request"]["url"]

    qs_evt = _scrub({"request": {"query_string": "next=/search%3Fphone%3D0812345678"}}, "BASIC")
    assert "0812345678" not in qs_evt["request"]["query_string"]

    # Operator rule applies to a token inside the nested URL too.
    tok_evt = _scrub(
        {"request": {"query_string": "next=/a%3Ftoken%3Dsupersecret"}},
        "BASIC",
        extra_mask=frozenset({"token"}),
    )
    assert "supersecret" not in tok_evt["request"]["query_string"]

    # Email inside an absolute nested URL is masked.
    abs_evt = _scrub(
        {"request": {"query_string": "next=https://x/y%3Femail%3Dalice@example.com"}}, "BASIC"
    )
    assert "alice@example.com" not in abs_evt["request"]["query_string"]


def test_nested_url_scrub_leaves_plain_query_values_intact() -> None:
    # Regression: values that aren't URLs keep the plain per-param behavior.
    result = _scrub({"request": {"query_string": "q=widgets&email=alice@example.com"}}, "BASIC")
    qs = result["request"]["query_string"]
    assert "q=widgets" in qs and "alice@example.com" not in qs


def test_scrubs_double_encoded_nested_redirect() -> None:
    # parse_qsl decodes only one layer, so a double-encoded redirect arrives
    # here still percent-encoded (%2Fsearch%3Fphone%3D…). Decode before deciding
    # there's no nested query so the inner phone is scrubbed.
    qs = _scrub(
        {"request": {"query_string": "next=%252Fsearch%253Fphone%253D0812345678"}}, "BASIC"
    )["request"]["query_string"]
    assert "0812345678" not in qs
    # Same class in a generic leaf (via the value walk / _scrub_keyed_value).
    ex = _scrub({"extra": {"redirect": "%2Fsearch%3Fphone%3D0812345678"}}, "BASIC")["extra"][
        "redirect"
    ]
    assert "0812345678" not in ex


def test_scrubs_triple_encoded_nested_redirect() -> None:
    qs = _scrub(
        {"request": {"query_string": "next=%25252Fsearch%25253Fphone%25253D0812345678"}}, "BASIC"
    )["request"]["query_string"]
    assert "0812345678" not in qs


def test_masks_emails_at_mixed_encoding_depths_in_one_leaf() -> None:
    # A single leaf can carry emails at different percent-encoding depths. The
    # value walk invokes the email backstop only once, so masking must not stop
    # after the first depth and leave the deeper (recoverable) address.
    leaf = _scrub({"extra": {"note": "alice%40example.com bob%2540example.com"}}, "BASIC")["extra"][
        "note"
    ]
    assert "alice@example.com" not in leaf and "alice%40example.com" not in leaf
    assert "bob@example.com" not in leaf and "bob%2540example.com" not in leaf


def test_scrubs_encoded_redirect_hidden_behind_visible_url_in_free_text() -> None:
    # A visible URL in a free-text leaf must not suppress bounded decoding of an
    # encoded redirect elsewhere in the same text — the encoded phone would
    # otherwise reach Sentry unchanged.
    note = _scrub(
        {"extra": {"note": "https://safe.test then %252Fsearch%253Fphone%253D0812345678"}}, "BASIC"
    )["extra"]["note"]
    assert "0812345678" not in note
    assert "https://" + "safe.test" in note  # the visible URL is preserved (no creds to strip)


def test_scrubs_encoded_redirect_after_visible_url_and_prose_punctuation() -> None:
    # An encoded redirect that follows a visible URL after prose punctuation (a
    # comma) sits *outside* the visible URL — `_URL_RE` stops at the comma — so the
    # hidden-encoded pass must still scrub it. Bounding the pass's exemption to the
    # actual `_URL_RE` match span (not merely an earlier `://` in the whitespace
    # token) keeps the encoded phone from reaching Sentry (PR #106 P1 review).
    note = _scrub(
        {"extra": {"note": "https://safe.test,%252Fsearch%253Fphone%253D0812345678"}}, "BASIC"
    )["extra"]["note"]
    assert "0812345678" not in note
    assert "https://" + "safe.test" in note  # the visible URL is preserved


@pytest.mark.parametrize("sep", ["!", "(", ":", "=", "*", "~"])
def test_scrubs_encoded_redirect_after_url_host_invalid_suffix(sep: str) -> None:
    # When prose punctuation `_URL_RE` does NOT exclude (`!`, `(`, `:`, `=`, …)
    # sits between the host and an encoded redirect, `_URL_RE` swallows the whole
    # token, so the encoded slice is inside the visible-URL span and exempted from
    # the hidden pass. `_scrub_url` must still reach the query buried in the netloc
    # (the path-separating `/` is encoded), not leak it (PR #106 P1 review).
    msg = _scrub(
        {"message": f"https://safe.test{sep}%252Fsearch%253Fphone%253D0812345678"}, "SENSITIVE"
    )["message"]
    assert "0812345678" not in msg


def test_scrubs_encoded_redirect_after_url_ipv6_bracket() -> None:
    # A `[` before the encoded redirect makes urlsplit raise (invalid IPv6), but
    # `_URL_RE` still matches the token, so the encoded slice is exempt from the
    # hidden pass. `_url_form_to_scrub` must hand `_scrub_url` the decoded form so
    # its ValueError fallback sees the now-literal `?` and redacts the query
    # instead of leaking it (PR #106 P1 review).
    for suffix in ["", "]"]:
        msg = _scrub(
            {"message": f"https://safe.test[%252Fsearch%253Fphone%253D0812345678{suffix}"},
            "SENSITIVE",
        )["message"]
        assert "0812345678" not in msg


def test_scrubs_nested_redirect_used_as_query_key() -> None:
    # A whole (percent-encoded) redirect used as a bare query *key*
    # (``?%252Fsearch%253Fphone%253D…`` → parse_qsl yields it as an empty-valued
    # key) must be recursively scrubbed like a value, so its inner params get the
    # field rules instead of being re-emitted decodable (PR #106 P1 review).
    msg = _scrub(
        {"message": "https://safe.test?%252Fsearch%253Fphone%253D0812345678"}, "SENSITIVE"
    )["message"]
    assert "0812345678" not in msg
    # An operator-configured sensitive key nested in the redirect key is masked too.
    msg2 = _scrub(
        {"message": "https://safe.test?%252Fsearch%253Ftoken%253Dsupersecret"},
        "SENSITIVE",
        extra_mask=frozenset({"token"}),
    )["message"]
    assert "supersecret" not in msg2


def test_redacts_nested_redirect_when_decode_limit_exhausted() -> None:
    from urllib.parse import quote

    nested = "/search?phone=0812345678"
    for _ in range(7):
        nested = quote(nested, safe="")

    qs = _scrub({"request": {"query_string": f"next={nested}"}}, "BASIC")["request"]["query_string"]
    assert qs == "next=%5BFiltered%5D"


def test_url_continues_through_paren_subdelim_to_later_query_param() -> None:
    # ``)`` is a valid URI query sub-delimiter; a URL token must continue through
    # it when followed by more ``&``-joined query so a later sensitive param is
    # scrubbed, not left raw (P1 leak).
    msg = _scrub({"message": "see https://host/p?x=f(a)&phone=0812345678 now"}, "SENSITIVE")[
        "message"
    ]
    assert "0812345678" not in msg
    # A prose paren with no following ``&query`` must NOT be swallowed into a URL.
    prose = _scrub({"message": "visit (https://host/p?x=1) today"}, "SENSITIVE")["message"]
    assert prose == "visit (https://host/p?x=1) today"


def test_conversational_prose_not_parsed_as_url() -> None:
    # A generic leaf with a stray ``?``/``#`` and a later ``=`` in prose must not
    # be structurally parsed as a URL (which would mangle it), while a genuine
    # bare/rootless query token is still scrubbed.
    def note(v: str) -> str:
        return _scrub({"extra": {"note": v}}, "SENSITIVE")["extra"]["note"]

    assert note("Are you sure? answer=yes") == "Are you sure? answer=yes"
    assert note("prefix# section=one") == "prefix# section=one"
    # genuine rootless query with PII is still scrubbed
    assert "0812345678" not in note("callback?phone=0812345678")


def test_free_text_url_nested_hash_field_scrubbed_once_and_consistent() -> None:
    # A visible URL in free text with a nested redirect carrying an EXTRA_HASH
    # field must hash that value exactly once — the hidden-encoded-URL pass must
    # not re-scrub the URL pass's already-scrubbed, re-encoded output, or the
    # message copy would desync from the request.url copy of the same value.
    event = {
        "request": {"url": "https://h/p?next=/a?token=supersecret"},
        "message": "go https://h/p?next=/a?token=supersecret now",
    }
    out = _scrub(event, "SENSITIVE", extra_hash=frozenset({"token"}))
    assert "supersecret" not in out["message"] and "supersecret" not in out["request"]["url"]
    # Same value → same single hash in both places.
    url_token = out["request"]["url"].split("token%3D", 1)[1]
    assert f"token%3D{url_token}" in out["message"]


def test_free_text_outer_url_stays_valid_when_nested_redirect_encoded() -> None:
    # A visible URL with an already-percent-encoded nested redirect must stay a
    # structurally valid outer URL after scrubbing — the hidden-encoded pass must
    # not decode the nested `next` value inside the recognized URL token and
    # splice literal `?`/`&` back into the outer query.
    msg = "GET https://app.test/login?next=%2Fsearch%3Fphone%3D0812345678"
    out = _scrub({"message": msg}, "SENSITIVE")["message"]
    assert "0812345678" not in out
    # Outer URL preserved; nested redirect stays percent-encoded in the query.
    assert out.startswith("GET https://app.test/login?next=%2Fsearch%3Fphone%3D")


def test_free_text_scheme_relative_outer_url_stays_valid_when_nested_encoded() -> None:
    # The exemption that leaves visible URLs to the `_URL_RE` pass must recognize
    # *all* forms `_URL_RE` accepts, including scheme-relative `//host/...` URLs
    # (which have `//` but no `://`). Bounding the exemption to `_URL_RE`'s match
    # span covers the scheme-relative form, so the hidden pass doesn't independently
    # decode the nested redirect: the outer URL stays structurally valid and the
    # nested phone is masked exactly once (PR #106 P2 review).
    msg = "GET //app.test/login?next=%2Fsearch%3Fphone%3D0812345678"
    out = _scrub({"message": msg}, "SENSITIVE")["message"]
    assert "0812345678" not in out
    assert out.startswith("GET //app.test/login?next=%2Fsearch%3Fphone%3D")


def test_free_text_deep_encoded_authority_redacted_wholesale() -> None:
    # A visible-scheme URL whose authority delimiters are encoded beyond the
    # decode cap must be redacted as a unit, not fragmented into a surviving
    # ``https://<username>`` prefix (issue #98).
    from urllib.parse import quote

    colon = at = ""
    depth = 6
    c, a = ":", "@"
    for _ in range(depth):
        c, a = quote(c, safe=""), quote(a, safe="")
    colon, at = c, a
    url = f"https://alicesecret{colon}pw{at}internal.test/dashboard"
    msg = _scrub({"message": f"redirecting to {url} now"}, "SENSITIVE")["message"]
    assert "alicesecret" not in msg
    assert msg == "redirecting to [Filtered] now"


def test_scrubs_scheme_relative_nested_redirect_credentials() -> None:
    # A scheme-relative //user:secret@host redirect value carries no http scheme
    # and no ?key=value, so it must still be routed through _scrub_url (which
    # redacts the user:pass@ credentials) rather than email-only masking.
    qs = _scrub({"request": {"query_string": "next=//user:secret@localhost/path"}}, "BASIC")[
        "request"
    ]["query_string"]
    assert "secret" not in qs
    ex = _scrub({"extra": {"redirect": "//user:secret@localhost/path"}}, "BASIC")["extra"][
        "redirect"
    ]
    assert "secret" not in ex


def test_masks_email_in_query_parameter_name() -> None:
    # PII in the query *key* (?alice@example.com=1) must be masked — urlencode
    # would otherwise re-emit the raw address.
    url = _scrub({"request": {"url": "/search?alice@example.com=1"}}, "BASIC")["request"]["url"]
    assert "alice@example.com" not in url and "alice%40example.com" not in url


def test_masks_email_in_span_http_query_under_nonsensitive_key() -> None:
    event = {"spans": [{"op": "http.client", "data": {"http.query": "q=dave@example.com&n=1"}}]}
    result = _scrub(event, "BASIC")
    q = result["spans"][0]["data"]["http.query"]
    assert "dave@example.com" not in q and "n=1" in q


def test_scrubs_nested_url_in_structured_query_string() -> None:
    # A nested URL value under a redirect param must be scrubbed in both the dict
    # and list-of-pairs query_string forms, not just the raw-string form.
    dict_evt = _scrub({"request": {"query_string": {"next": "/search?phone=0812345678"}}}, "BASIC")
    assert "0812345678" not in dict_evt["request"]["query_string"]["next"]

    list_evt = _scrub(
        {"request": {"query_string": [["next", "/search?phone=0812345678"]]}}, "BASIC"
    )
    assert "0812345678" not in dict(list_evt["request"]["query_string"])["next"]


def test_scrubs_raw_json_pair_list_body() -> None:
    # A raw JSON body that decodes to a list of [key, value] pairs is form data,
    # so the key rules must apply (Authorization dropped, phone masked) — not
    # positional scrubbing that leaves them raw.
    import json

    event = {
        "request": {"data": json.dumps([["authorization", "Bearer x"], ["phone", "0812345678"]])}
    }
    result = _scrub(event, "BASIC")
    data = json.loads(result["request"]["data"])
    keys = [k for k, _ in data]
    assert "authorization" not in keys  # dropped
    assert ["phone", "08***"] in data


def test_scrubs_nested_url_in_list_valued_dict_query_string() -> None:
    # A dict query_string whose value is a list (repeated params like
    # ``?tag=a&tag=b&next=/search?phone=123``) must have each element
    # checked for nested URL scrubbing — not only the string-valued paths.
    event = {
        "request": {
            "query_string": {"tag": ["a", "b"], "next": ["/search?phone=0812345678"], "q": "plain"}
        }
    }
    result = _scrub(event, "BASIC")
    qs = result["request"]["query_string"]
    assert "0812345678" not in str(qs)
    assert qs["tag"] == ["a", "b"]  # non-PII list values preserved
    assert "plain" in qs.get("q", "")
    # Email inside a nested URL in a list value is also scrubbed.
    email_evt = _scrub(
        {"request": {"query_string": {"urls": ["https://x/y?email=alice@example.com"]}}}, "BASIC"
    )
    assert "alice@example.com" not in str(email_evt["request"]["query_string"]["urls"])


def test_scrubs_list_of_pair_query_string() -> None:
    event = {
        "request": {"query_string": [["email", "alice@example.com"], ["tag", "a"], ["tag", "b"]]}
    }
    result = _scrub(event, "BASIC")
    qs = result["request"]["query_string"]
    assert ["email", "a***@example.com"] in qs
    assert ["tag", "a"] in qs and ["tag", "b"] in qs  # repeated keys preserved


def test_scrubs_tags_dict() -> None:
    event = {"tags": {"email": "alice@example.com", "release": "1.2.3"}}
    result = _scrub(event, "BASIC")
    assert result["tags"]["email"] == "a***@example.com"
    assert result["tags"]["release"] == "1.2.3"


def test_scrubs_tags_list_form() -> None:
    event = {"tags": [["email", "bob@example.com"], ["env", "prod"]]}
    result = _scrub(event, "BASIC")
    assert ["email", "b***@example.com"] in result["tags"]
    assert ["env", "prod"] in result["tags"]


def test_masks_query_string_dict() -> None:
    event = {"request": {"query_string": {"email": "bob@example.com", "q": "widgets"}}}
    result = _scrub(event, "BASIC")
    qs = result["request"]["query_string"]
    assert qs["email"] == "b***@example.com"
    assert qs["q"] == "widgets"


def test_masks_request_data_body() -> None:
    event = {"request": {"data": {"email": "carol@example.com", "note": {"phone": "0812345678"}}}}
    result = _scrub(event, "BASIC")
    data = result["request"]["data"]
    assert data["email"] == "c***@example.com"
    assert data["note"]["phone"].endswith("***")


def test_scrubs_pair_list_request_data() -> None:
    # Parsed form data as [[key, value], …]: key rules must apply (Authorization
    # dropped, phone masked) — sanitize_body would treat it as positional.
    event = {
        "request": {
            "data": [
                ["authorization", "Bearer secret"],
                ["phone", "0812345678"],
                ["email", "alice@example.com"],
                ["note", "hello"],
            ]
        }
    }
    result = _scrub(event, "BASIC")
    data = result["request"]["data"]
    keys = [k for k, _ in data]
    assert "authorization" not in keys  # dropped
    assert ["phone", "08***"] in data
    assert ["email", "a***@example.com"] in data
    assert ["note", "hello"] in data  # non-PII preserved


def test_request_data_honors_explicit_field_rule() -> None:
    # An operator rule targeting the whole "data" body wins over per-field body
    # scrubbing — a non-email secret would otherwise survive.
    drop = _scrub(
        {"request": {"data": {"token": "supersecret"}}}, "BASIC", extra_drop=frozenset({"data"})
    )
    assert "data" not in drop["request"]

    mask = _scrub(
        {"request": {"data": {"email": "alice@example.com", "token": "supersecret"}}},
        "BASIC",
        extra_mask=frozenset({"data"}),
    )
    assert mask["request"]["data"] == "[Filtered]"

    # No rule → the body is still scrubbed per field (email masked).
    norule = _scrub({"request": {"data": {"email": "alice@example.com"}}}, "BASIC")
    assert norule["request"]["data"]["email"] == "a***@example.com"


def test_positional_list_request_data_not_treated_as_pairs() -> None:
    # A genuine positional JSON-array body keeps normal container scrubbing
    # (nested dict keys masked) and is not misread as pairs.
    event = {"request": {"data": [{"phone": "0812345678"}, "note"]}}
    result = _scrub(event, "BASIC")
    data = result["request"]["data"]
    assert data[0]["phone"].endswith("***")
    assert data[1] == "note"


def test_scrubs_pair_list_nested_in_extra() -> None:
    # A pair-shaped list nested under extra/contexts (set_extra("form", [[...]]))
    # is scrubbed by key too, not just the top-level request.data pair form.
    event = {"extra": {"form": [["authorization", "Bearer x"], ["phone", "0812345678"]]}}
    result = _scrub(event, "BASIC")
    form = result["extra"]["form"]
    assert [k for k, _ in form] == ["phone"]  # authorization dropped
    assert form == [["phone", "08***"]]


def test_scrubs_url_keyed_pair_nested_in_extra() -> None:
    # A URL/query-keyed pair under extra must get URL scrubbing (its inner query
    # params), not just the drop/mask/hash-by-key rules.
    event = {"extra": {"links": [["url", "/search?phone=0812345678"]]}}
    result = _scrub(event, "BASIC")
    assert "0812345678" not in result["extra"]["links"][0][1]


def test_url_pair_in_extra_hashed_once_like_request_url() -> None:
    # EXTRA_HASH_FIELDS={"url"} at SENSITIVE: a url pair under extra is hashed
    # exactly once by the walk — the same single sha256 as request.url — not
    # double-hashed by both the pair pass and the walk.
    url = "https://app.test/p?x=1"
    eh = frozenset({"url"})
    req = _scrub({"request": {"url": url}}, "SENSITIVE", extra_hash=eh)["request"]["url"]
    pair = _scrub({"extra": {"links": [["url", url]]}}, "SENSITIVE", extra_hash=eh)["extra"][
        "links"
    ][0][1]
    assert len(req) == 64 and pair == req


def test_scrubs_non_http_uri_credentials() -> None:
    # A non-http URI value (e.g. a postgres DSN, redis://, ftp://) with
    # userinfo must have its credentials redacted by _scrub_url — not only
    # email-masked, which would miss ``user:secret@`` in internal hosts.
    pg = _scrub({"extra": {"dsn": "postgres://user:secret@localhost:5432/db"}}, "BASIC")["extra"][
        "dsn"
    ]
    assert "secret" not in pg and "[Filtered]@localhost" in pg

    redis = _scrub({"extra": {"url": "redis://:password@host:6379/0"}}, "BASIC")["extra"]["url"]
    assert "password" not in redis and "[Filtered]@host" in redis


def test_scrubs_all_urls_in_url_prefixed_free_text() -> None:
    event = {"extra": {"note": "https://safe.test then postgres://user:secret@localhost/db"}}
    result = _scrub(event, "BASIC")
    note = result["extra"]["note"]
    assert "https://" + "safe.test" in note
    assert "user:secret" not in note
    assert "postgres://" + "[Filtered]@localhost/db" in note


def test_scrubs_all_urls_in_url_prefixed_nested_query_value() -> None:
    event = {
        "request": {
            "query_string": (
                "next=https%3A%2F%2Fsafe.test%20then%20"
                "postgres%3A%2F%2Fuser%3Asecret%40localhost%2Fdb"
            )
        }
    }
    result = _scrub(event, "BASIC")
    query_string = result["request"]["query_string"]
    assert "https%3A%2F%2F" + "safe.test" in query_string
    assert "user%3Asecret" not in query_string
    assert "%5BFiltered%5D%40localhost%2Fdb" in query_string


def test_scrubs_punctuation_separated_urls_in_free_text() -> None:
    event = {"extra": {"note": "https://safe.test,postgres://user:secret@localhost/db"}}
    result = _scrub(event, "BASIC")
    note = result["extra"]["note"]
    assert "https://" + "safe.test" in note
    assert "user:secret" not in note
    assert "postgres://" + "[Filtered]@localhost/db" in note


def test_scrubs_closing_punctuation_separated_urls_in_free_text() -> None:
    event = {"extra": {"note": "https://safe.test)postgres://alice:secret@localhost/db"}}
    result = _scrub(event, "BASIC")
    note = result["extra"]["note"]
    assert "https://" + "safe.test" in note
    assert ")" in note
    assert "alice:secret" not in note
    assert "postgres://" + "[Filtered]@localhost/db" in note


def test_scrubs_adjacent_urls_split_at_subsequent_scheme() -> None:
    event = {"extra": {"note": "https://safe.test(postgres://alice:secret@localhost/db"}}
    result = _scrub(event, "BASIC")
    note = result["extra"]["note"]
    assert "https://" + "safe.test" in note
    assert "(" in note
    assert "alice:secret" not in note
    assert "postgres://" + "[Filtered]@localhost/db" in note


def test_scrubs_mixed_case_url_scheme_in_free_text() -> None:
    # A mixed-case scheme (HTTPS://) in a message is still a URL — its query must
    # be scrubbed, not left raw because the scheme wasn't lowercase.
    result = _scrub({"message": "see HTTPS://app.test/search?phone=0812345678 x"}, "BASIC")
    assert "0812345678" not in result["message"]


def test_prescrubbed_query_fields_hashed_once_like_request_url() -> None:
    # At SENSITIVE with EXTRA_HASH_FIELDS={"token"}, a nested hashed param inside
    # request.query_string / env.QUERY_STRING / a URL header must be hashed
    # exactly once — the same single sha256 as request.url — not re-hashed by the
    # final walk after the request block already scrubbed it.
    import hashlib

    eh = frozenset({"token"})
    single = hashlib.sha256(b"pepper" + b"supersecret").hexdigest()
    double = hashlib.sha256(b"pepper" + single.encode()).hexdigest()

    def _out(event):
        return str(_scrub(event, "SENSITIVE", extra_hash=eh))

    cases = [
        {"request": {"url": "/a?token=supersecret"}},
        {"request": {"query_string": "next=/a%3Ftoken%3Dsupersecret"}},
        {"request": {"query_string": {"next": "/a?token=supersecret"}}},
        {"request": {"query_string": [["next", "/a?token=supersecret"]]}},
        {"request": {"env": {"QUERY_STRING": "next=/a%3Ftoken%3Dsupersecret"}}},
        {"request": {"headers": {"Referer": "https://h/p?token=supersecret"}}},
    ]
    # Transaction name and non-DB span descriptions are also pre-scrubbed
    # before the walk — they must not be re-hashed (sa  me single-hash rule).
    trace_cases = [
        {"transaction": "/a?token=supersecret"},
        {"spans": [{"op": "http.client", "description": "/a?token=supersecret"}]},
    ]

    for event in cases + trace_cases:
        out = _out(event)
        assert single in out and double not in out
        assert "supersecret" not in out


def test_prescrubbed_request_fields_still_get_email_backstop() -> None:
    # Keeping the query_string / env / headers out of the walk must not lose the
    # email backstop for a plain email in a non-URL value.
    qs = _scrub({"request": {"query_string": "q=alice@example.com"}}, "BASIC")["request"][
        "query_string"
    ]
    assert "alice@example.com" not in qs
    hdr = _scrub({"request": {"headers": {"X-User": "alice@example.com"}}}, "BASIC")["request"][
        "headers"
    ]
    assert "alice@example.com" not in str(hdr)
    env = _scrub({"request": {"env": {"HTTP_X_USER": "alice@example.com"}}}, "BASIC")["request"][
        "env"
    ]
    assert "alice@example.com" not in str(env)


def test_masks_dict_key_in_prescrubbed_query_string() -> None:
    # A dict query_string whose key is an email must have the key masked,
    # matching the per-parameter key masking in _scrub_query_string.
    event = {"request": {"query_string": {"alice@example.com": "1", "q": "widgets"}}}
    result = _scrub(event, "BASIC")
    qs = result["request"]["query_string"]
    assert "alice@example.com" not in str(qs)
    assert "a***@example.com" in str(qs)
    assert qs["q"] == "widgets"  # non-PII key/value preserved


def test_scrubs_raw_json_body_string() -> None:
    import json

    event = {"request": {"data": '{"email": "alice@example.com", "n": 1}'}}
    result = _scrub(event, "BASIC")
    parsed = json.loads(result["request"]["data"])  # still valid JSON
    assert parsed["email"] == "a***@example.com"
    assert parsed["n"] == 1


def test_scrubs_multiply_encoded_email_in_raw_json_body_string() -> None:
    import json

    event = {"request": {"data": json.dumps({"note": "alice%2540example.com"})}}
    result = _scrub(event, "BASIC")
    parsed = json.loads(result["request"]["data"])
    assert parsed["note"] == "a***@example.com"


def test_scrubs_mixed_depth_encoded_email_in_generic_leaf() -> None:
    event = {"extra": {"note": "alice%40example%252Ecom"}}
    result = _scrub(event, "BASIC")
    note = result["extra"]["note"]
    assert "alice@example.com" not in note
    assert "alice%40example%252Ecom" not in note
    assert note == "a***@example.com"


def test_raw_json_body_with_url_string_not_corrupted_by_walk() -> None:
    # A raw JSON body containing a URL-like string must stay valid JSON — the
    # whole-event walk must not re-detect the serialized document as a URL and
    # corrupt it with _scrub_url.
    import json

    event = {"request": {"data": json.dumps({"next": "/search?phone=0812345678", "x": 1})}}
    result = _scrub(event, "BASIC")
    body = result["request"]["data"]
    parsed = json.loads(body)  # still valid JSON, not URL-mangled
    assert parsed["x"] == 1
    assert "0812345678" not in body  # inner query param still scrubbed


def test_scrubs_raw_form_body_string() -> None:
    event = {"request": {"data": "email=alice@example.com&x=1"}}
    result = _scrub(event, "BASIC")
    body = result["request"]["data"]
    assert "alice@example.com" not in body and "alice%40example.com" not in body
    assert "x=1" in body


def test_hashed_url_field_not_double_hashed_across_locations() -> None:
    # With EXTRA_HASH_FIELDS={"url"} at SENSITIVE, a URL field inside a
    # body-scrubbed container (extra / contexts / request.data, incl. a raw JSON
    # body) must be hashed exactly once — the same single sha256 as request.url,
    # not the walk re-hashing the body pass's digest. Consistency lets hashed PII
    # be correlated across fields and sinks.
    import json

    url = "https://app.test/p?x=1"
    eh = frozenset({"url"})
    req = _scrub({"request": {"url": url}}, "SENSITIVE", extra_hash=eh)["request"]["url"]
    extra = _scrub({"extra": {"url": url}}, "SENSITIVE", extra_hash=eh)["extra"]["url"]
    ctx = _scrub({"contexts": {"c": {"url": url}}}, "SENSITIVE", extra_hash=eh)["contexts"]["c"][
        "url"
    ]
    data = _scrub({"request": {"data": {"url": url}}}, "SENSITIVE", extra_hash=eh)["request"][
        "data"
    ]["url"]
    raw = json.loads(
        _scrub({"request": {"data": json.dumps({"url": url})}}, "SENSITIVE", extra_hash=eh)[
            "request"
        ]["data"]
    )["url"]
    assert len(req) == 64  # single sha256 hex
    assert req != url
    assert extra == req == ctx == data == raw


def test_unparseable_raw_body_redacted() -> None:
    event = {"request": {"data": "alice@example.com is here in free text"}}
    result = _scrub(event, "BASIC")
    assert result["request"]["data"] == "[Filtered]"


def test_json_scalar_raw_body_redacted_not_reparsed_as_form() -> None:
    # A valid JSON scalar body (a quoted string / number) is not a form body —
    # reparsing "phone=0812345678" as k=v would leak the number. Redact instead.
    import json

    str_evt = _scrub({"request": {"data": json.dumps("phone=0812345678")}}, "BASIC")
    assert str_evt["request"]["data"] == "[Filtered]"
    num_evt = _scrub({"request": {"data": "123"}}, "BASIC")
    assert num_evt["request"]["data"] == "[Filtered]"
    # Regression: a genuine url-encoded form body is still scrubbed per-pair.
    form_evt = _scrub({"request": {"data": "email=alice@example.com&x=1"}}, "BASIC")
    assert "alice@example.com" not in form_evt["request"]["data"]
    assert "x=1" in form_evt["request"]["data"]


def test_scrubs_custom_contexts() -> None:
    event = {
        "contexts": {
            "account": {"email": "alice@example.com"},
            "trace": {"trace_id": "abc", "span_id": "def"},
        }
    }
    result = _scrub(event, "BASIC")
    assert result["contexts"]["account"]["email"] == "a***@example.com"
    assert result["contexts"]["trace"]["trace_id"] == "abc"  # non-PII preserved


def test_scrubs_extra() -> None:
    event = {"extra": {"email": "dave@example.com", "count": 3}}
    result = _scrub(event, "BASIC")
    assert result["extra"]["email"] == "d***@example.com"
    assert result["extra"]["count"] == 3


def test_masks_email_under_nonsensitive_key_across_containers() -> None:
    # An email under a key NOT in MASK_FIELDS must still be masked wherever it
    # sits — tags, extra, contexts, and header/dict values — not just in queries.
    event = {
        "tags": {"owner": "alice@example.com"},
        "extra": {"note": "ping bob@example.com now"},
        "contexts": {"order": {"contact": "carol@example.com"}},
        "request": {"headers": {"X-Owner": "dave@example.com"}},
    }
    result = _scrub(event, "BASIC")
    assert result["tags"]["owner"] == "a***@example.com"
    assert "bob@example.com" not in result["extra"]["note"]
    assert result["contexts"]["order"]["contact"] == "c***@example.com"
    assert result["request"]["headers"]["X-Owner"] == "d***@example.com"


def test_masks_percent_encoded_email_in_freetext_and_generic_leaves() -> None:
    # A %40-encoded email in a free-text field (message) or a generic string leaf
    # (extra) that isn't routed through a URL/query parser must still be masked.
    event = {
        "message": "user=alice%40example.com logged in",
        "extra": {"owner": "bob%40example.com"},
    }
    result = _scrub(event, "BASIC")
    msg = result["message"]
    assert "alice%40example.com" not in msg and "alice@example.com" not in msg
    assert "a***@example.com" in msg
    owner = result["extra"]["owner"]
    assert "bob%40example.com" not in owner and owner == "b***@example.com"


def test_masks_percent_encoded_email_with_encoded_local_part() -> None:
    # The local part may itself be percent-encoded (a plus-addressed
    # ``alice.smith%2Btag`` or an encoded dot ``%2E``). The mask must swallow the
    # *whole* token — no ``alice.smith%2B`` prefix may survive next to the mask.
    event = {
        "extra": {"plus": "alice.smith%2Btag%40example.com", "dot": "jane%2Edoe%40example.com"}
    }
    result = _scrub(event, "BASIC")
    plus = result["extra"]["plus"]
    assert plus == "a***@example.com"
    assert "alice" not in plus and "%2B" not in plus and "%40" not in plus
    dot = result["extra"]["dot"]
    assert dot == "j***@example.com"
    assert "jane" not in dot and "%2E" not in dot


def test_masks_percent_encoded_email_with_encoded_domain_dot() -> None:
    # The domain dot may itself be percent-encoded (``example%2Ecom``); the
    # address must still be masked, not left recoverable.
    event = {"extra": {"d": "alice%40example%2Ecom", "sub": "jane%2Edoe%40sub%2Eexample%2Ecom"}}
    result = _scrub(event, "BASIC")
    assert result["extra"]["d"] == "a***@example.com"
    assert "%2E" not in result["extra"]["d"] and "%40" not in result["extra"]["d"]
    assert result["extra"]["sub"] == "j***@sub.example.com"


def test_masks_percent_encoded_email_with_encoded_tld() -> None:
    # The TLD letters may themselves be percent-encoded (``%63%6f%6d`` == "com");
    # the address must still be masked.
    result = _scrub({"extra": {"o": "alice%40example%2E%63%6f%6d"}}, "BASIC")
    assert result["extra"]["o"] == "a***@example.com"
    assert "%63" not in result["extra"]["o"]


def test_scrubs_url_looking_positional_list_element() -> None:
    # A URL / relative URL stored as a bare list element (positional array) must
    # have its query scrubbed too, like dict/pair values do.
    data_evt = _scrub({"request": {"data": ["/search?phone=0812345678"]}}, "BASIC")
    assert "0812345678" not in data_evt["request"]["data"][0]

    extra_evt = _scrub({"extra": {"redirects": ["/search?phone=0812345678", "plain"]}}, "BASIC")
    assert "0812345678" not in extra_evt["extra"]["redirects"][0]
    assert extra_evt["extra"]["redirects"][1] == "plain"  # non-URL leaf untouched


def test_masks_email_in_list_string_leaf() -> None:
    event = {"extra": {"cc": ["erin@example.com", "ok"]}}
    result = _scrub(event, "BASIC")
    assert result["extra"]["cc"][0] == "e***@example.com"
    assert result["extra"]["cc"][1] == "ok"


def test_recurses_into_tuple_valued_fields() -> None:
    # set_extra("cc", ("alice@example.com",)) / tuple nested in a context: the
    # value walk must normalise tuples so their string leaves get scrubbed.
    event = {
        "extra": {"cc": ("alice@example.com", "ok")},
        "contexts": {"order": {"items": ({"email": "bob@example.com"},)}},
    }
    result = _scrub(event, "BASIC")
    assert list(result["extra"]["cc"]) == ["a***@example.com", "ok"]
    assert "bob@example.com" not in str(result["contexts"]["order"]["items"])


def test_applies_key_rules_inside_tuple_of_dicts() -> None:
    # A tuple of dicts must get the key-based rules (phone masked, authorization
    # dropped) just like the same dict inside a list — not only email masking.
    event = {"extra": {"items": ({"phone": "0812345678", "authorization": "Bearer x", "ok": "1"},)}}
    result = _scrub(event, "BASIC")
    item = list(result["extra"]["items"])[0]
    assert item["phone"].endswith("***")  # MASK_FIELD masked
    assert "authorization" not in item  # DROP_HEADERS dropped
    assert item["ok"] == "1"  # non-sensitive preserved


def test_tuple_contained_url_field_hashed_once() -> None:
    # A url field inside a tuple/set (normalised by the walk, no earlier body
    # pass) must be hashed exactly once at SENSITIVE — the same single sha256 as
    # request.url — not double-hashed by the normalisation and the walk.
    url = "https://app.test/p?x=1"
    eh = frozenset({"url"})
    req = _scrub({"request": {"url": url}}, "SENSITIVE", extra_hash=eh)["request"]["url"]
    tup = _scrub({"extra": ({"url": url},)}, "SENSITIVE", extra_hash=eh)["extra"][0]["url"]
    st = _scrub({"extra": {"cc": ({"url": url},)}}, "SENSITIVE", extra_hash=eh)["extra"]["cc"][0][
        "url"
    ]
    assert len(req) == 64 and tup == req == st


def test_recurses_into_set_valued_fields() -> None:
    # set_extra("cc", {"alice@example.com"}) / a frozenset nested in a context:
    # Sentry serialises set databags, so their string leaves must be scrubbed.
    event = {
        "extra": {"cc": {"alice@example.com"}},
        "contexts": {"order": {"tags": frozenset({"bob@example.com"})}},
    }
    result = _scrub(event, "BASIC")
    assert "alice@example.com" not in str(result["extra"]["cc"])
    assert "bob@example.com" not in str(result["contexts"]["order"]["tags"])


def test_applies_key_rules_inside_set_of_dicts() -> None:
    # A set can hold hashable items; a set nested via a list of dicts must get
    # the key-based rules too (phone masked) — not only email masking.
    event = {"extra": {"items": ({"phone": "0812345678"},)}}  # tuple carrying a dict
    # Also cover a bare set of emails masked by the walk.
    event["extra"]["emails"] = {"carol@example.com"}
    result = _scrub(event, "BASIC")
    assert list(result["extra"]["items"])[0]["phone"].endswith("***")
    assert "carol@example.com" not in str(result["extra"]["emails"])


def test_mask_emails_in_leaves_covers_set_and_frozenset() -> None:
    # The email-backstop leaf walker (used to restore already-scrubbed saved
    # request fields) must handle set/frozenset databags like every other
    # container, so an email in a set-valued saved field can't slip past it.
    from observe_kit.sentry.config import _mask_emails_in_leaves

    scrubbed_set = _mask_emails_in_leaves({"alice@example.com"})
    assert isinstance(scrubbed_set, set)
    assert all("alice@example.com" not in v and "a***@example.com" in v for v in scrubbed_set)

    scrubbed_frozen = _mask_emails_in_leaves(frozenset({"bob@example.com"}))
    assert isinstance(scrubbed_frozen, frozenset)
    assert all("bob@example.com" not in v and "b***@example.com" in v for v in scrubbed_frozen)


def test_scrubs_message_email_and_url() -> None:
    event = {
        "message": "login for email=alice@example.com via https://app.test/x?email=bob@example.com"
    }
    result = _scrub(event, "BASIC")
    msg = result["message"]
    assert "alice@example.com" not in msg
    assert "a***@example.com" in msg
    assert "bob@example.com" not in msg  # email inside the URL query masked too


def test_scrubs_scheme_relative_url_in_free_text() -> None:
    # A scheme-relative //authority URL with userinfo (no query) must have its
    # user:pass@ credentials redacted, and its query scrubbed when present.
    cred = _scrub({"message": "conn //user:secret@localhost/path done"}, "BASIC")["message"]
    assert "secret" not in cred and "[Filtered]@localhost" in cred

    query = _scrub({"message": "hit //host/x?phone=0812345678"}, "BASIC")["message"]
    assert "0812345678" not in query


def test_scrubs_relative_url_query_in_free_text() -> None:
    # A relative URL in a message carries the same query PII as an absolute one;
    # its query must be scrubbed too (phone masked, operator token rule applied).
    result = _scrub({"message": "GET /search?phone=0812345678 done"}, "BASIC")
    assert "0812345678" not in result["message"]
    assert "/search?phone=" in result["message"]  # path preserved, value masked

    tok = _scrub(
        {"message": "hit /api?token=supersecret&x=1"}, "BASIC", extra_mask=frozenset({"token"})
    )
    assert "supersecret" not in tok["message"] and "x=1" in tok["message"]


def test_relative_url_scrub_leaves_prose_untouched() -> None:
    # A "/word?word" token without a key=value query is ordinary prose, not a URL.
    result = _scrub({"message": "read and/or?maybe later"}, "BASIC")
    assert result["message"] == "read and/or?maybe later"


def test_scrubs_logentry_message_and_formatted() -> None:
    event = {"logentry": {"message": "u=%s", "formatted": "u=carol@example.com"}}
    result = _scrub(event, "BASIC")
    assert result["logentry"]["formatted"] == "u=c***@example.com"
    assert result["logentry"]["message"] == "u=%s"  # no PII, unchanged


def test_text_field_honors_explicit_mask() -> None:
    # EXTRA_MASK_FIELDS={"message"} must mask the whole field — a non-email
    # secret (token=supersecret) that _scrub_text alone would leave survives
    # otherwise. Applies to top-level, breadcrumb, and logentry messages.
    event = {
        "message": "token=supersecret",
        "logentry": {"formatted": "token=anothersecret"},
        "breadcrumbs": {"values": [{"message": "token=crumbsecret"}]},
    }
    result = _scrub(event, "BASIC", extra_mask=frozenset({"message", "formatted"}))
    assert result["message"] == "[Filtered]"
    assert "anothersecret" not in result["logentry"]["formatted"]
    assert "crumbsecret" not in result["breadcrumbs"]["values"][0]["message"]


def test_text_field_explicit_mask_still_scrubs_all_emails() -> None:
    event = {"message": "alice@example.com bob@example.com"}
    result = _scrub(event, "BASIC", extra_mask=frozenset({"message"}))
    assert "alice@example.com" not in result["message"]
    assert "bob@example.com" not in result["message"]


def test_text_field_honors_explicit_drop() -> None:
    # EXTRA_DROP_HEADERS={"message"} redacts the field wholesale.
    event = {"message": "token=supersecret"}
    result = _scrub(event, "BASIC", extra_drop=frozenset({"message"}))
    assert result["message"] == "[Filtered]"


def test_scrubs_non_http_dsn_userinfo_in_free_text() -> None:
    event = {
        "message": "connect postgres://user:secret@localhost/db",
        "exception": {"values": [{"value": "redis://:password@cache.local/0 failed"}]},
    }
    result = _scrub(event, "BASIC")
    assert "user:secret" not in result["message"]
    assert result["message"] == "connect postgres://[Filtered]@localhost/db"
    value = result["exception"]["values"][0]["value"]
    assert ":password" not in value
    assert value == "redis://[Filtered]@cache.local/0 failed"


def test_text_field_without_rule_still_pattern_scrubbed() -> None:
    # Regression: with no operator rule, free-text scrubbing still runs (email
    # masked) and a non-secret message is otherwise untouched.
    event = {"message": "hello alice@example.com"}
    result = _scrub(event, "BASIC")
    assert "alice@example.com" not in result["message"]
    assert "a***@example.com" in result["message"]


def test_scrubs_logentry_params() -> None:
    # logger.error("user=%s", email) keeps the raw arg in logentry.params even
    # after the formatted text is scrubbed.
    event = {
        "logentry": {
            "message": "user=%s at %s",
            "params": ["grace@example.com", "https://app.test/x?email=heidi@example.com"],
        }
    }
    result = _scrub(event, "BASIC")
    params = result["logentry"]["params"]
    assert params[0] == "g***@example.com"
    assert "heidi@example.com" not in params[1]  # email inside the URL query masked too


def test_scrubs_logentry_params_pair_list_keys() -> None:
    event = {
        "logentry": {"params": [["alice@example.com", "1"], ["url", "/x?email=bob@example.com"]]}
    }
    result = _scrub(event, "BASIC")
    params = result["logentry"]["params"]
    assert params[0] == ["a***@example.com", "1"]
    assert params[1][0] == "url"
    assert "bob@example.com" not in params[1][1]


def test_scrubs_logentry_params_tuple() -> None:
    # Sentry copies params straight from record.args, which is a tuple for
    # positional interpolation — _scrub_all_text would skip a tuple.
    event = {"logentry": {"message": "user=%s", "params": ("alice@example.com",)}}
    result = _scrub(event, "BASIC")
    params = result["logentry"]["params"]
    assert list(params) == ["a***@example.com"]


def test_scrubs_logentry_params_named_dict() -> None:
    # Named interpolation gives a dict; key-based rules must apply (phone masked).
    event = {"logentry": {"message": "u=%(phone)s", "params": {"phone": "0812345678"}}}
    result = _scrub(event, "BASIC")
    assert result["logentry"]["params"]["phone"].endswith("***")


def test_scrubs_logentry_params_nested_tuple() -> None:
    # A named arg can itself be a tuple; its leaves must not be skipped.
    event = {"logentry": {"message": "cc=%(emails)s", "params": {"emails": ("alice@example.com",)}}}
    result = _scrub(event, "BASIC")
    assert "alice@example.com" not in str(result["logentry"]["params"]["emails"])


def test_redacts_statement_keys_in_logentry_params() -> None:
    # A named logentry.params arg keyed by a DB statement / bind-param key must be
    # redacted (as span data is) — not only pattern-scrubbed for emails/URLs.
    stmt = _scrub({"logentry": {"params": {"db.statement": "SELECT phone='0812345678'"}}}, "BASIC")[
        "logentry"
    ]["params"]
    assert stmt["db.statement"] == "[Filtered]"
    param = _scrub({"logentry": {"params": {"db.query.parameter.phone": ["0812345678"]}}}, "BASIC")[
        "logentry"
    ]["params"]
    assert param["db.query.parameter.phone"] == "[Filtered]"


def test_scrubs_url_query_keys_in_logentry_params() -> None:
    # A named logentry.params arg keyed by a URL / bare-query key must get the
    # URL/query field rules (phone masked), not just free-text scrubbing.
    q = _scrub({"logentry": {"params": {"http.query": "phone=0812345678"}}}, "BASIC")["logentry"][
        "params"
    ]
    assert "0812345678" not in q["http.query"]
    u = _scrub({"logentry": {"params": {"url": "/search?phone=0812345678"}}}, "BASIC")["logentry"][
        "params"
    ]
    assert "0812345678" not in u["url"]


def test_params_subtree_honors_explicit_field_rule() -> None:
    # An operator rule targeting the whole "params" field must win over
    # pattern-scrubbing its leaves — a positional ["supersecret"] has no key or
    # email/URL pattern to catch otherwise.
    drop = _scrub(
        {"logentry": {"message": "u=%s", "params": ["supersecret"]}},
        "BASIC",
        extra_drop=frozenset({"params"}),
    )
    assert drop["logentry"]["params"] == "[Filtered]"

    mask = _scrub(
        {"logentry": {"params": ["supersecret"]}}, "BASIC", extra_mask=frozenset({"params"})
    )
    assert "supersecret" not in str(mask["logentry"]["params"])

    # No rule → the subtree is still pattern-scrubbed (embedded email masked).
    norule = _scrub({"logentry": {"message": "u=%s", "params": ["alice@example.com"]}}, "BASIC")
    assert norule["logentry"]["params"] == ["a***@example.com"]


def test_params_explicit_mask_still_scrubs_all_emails() -> None:
    event = {"logentry": {"params": ["alice@example.com", "bob@example.com"]}}
    result = _scrub(event, "BASIC", extra_mask=frozenset({"params"}))
    params = result["logentry"]["params"]
    assert params == "[Filtered]"


def test_params_field_rule_applied_once_in_extra_and_logentry() -> None:
    # EXTRA_HASH_FIELDS={"params"} at SENSITIVE hashes the field exactly once,
    # whether it sits in logentry (no body pass) or extra (body-scrubbed) — the
    # same single sha256, not double-hashed.
    le = _scrub(
        {"logentry": {"params": ["supersecret"]}}, "SENSITIVE", extra_hash=frozenset({"params"})
    )["logentry"]["params"]
    ex = _scrub(
        {"extra": {"params": ["supersecret"]}}, "SENSITIVE", extra_hash=frozenset({"params"})
    )["extra"]["params"]
    assert len(le) == 64 and le == ex


def test_pair_list_params_non_string_value_honors_explicit_field_rule() -> None:
    def event() -> dict[str, object]:
        return {"extra": {"items": [["params", ["supersecret"]]]}}

    dropped = _scrub(event(), "BASIC", extra_drop=frozenset({"params"}))
    assert dropped["extra"]["items"] == [["params", "[Filtered]"]]

    masked = _scrub(event(), "BASIC", extra_mask=frozenset({"params"}))
    assert masked["extra"]["items"] == [["params", "[Filtered]"]]

    hashed = _scrub(event(), "SENSITIVE", extra_hash=frozenset({"params"}))
    value = hashed["extra"]["items"][0][1]
    assert "supersecret" not in str(value)
    assert isinstance(value, str) and len(value) == 64


def test_applies_key_rules_inside_logentry_params_nested_tuple() -> None:
    # A named param whose value is a tuple of dicts must get key-based rules
    # (phone masked, authorization dropped), not only email pattern-scrubbing.
    event = {
        "logentry": {
            "message": "x=%(items)s",
            "params": {"items": ({"phone": "0812345678", "authorization": "Bearer x"},)},
        }
    }
    result = _scrub(event, "BASIC")
    item = list(result["logentry"]["params"]["items"])[0]
    assert item["phone"].endswith("***")
    assert "authorization" not in item


def test_scrubs_exception_value_text() -> None:
    # A raised error whose message embeds PII: ValueError(f"email={email}").
    event = {
        "exception": {
            "values": [{"type": "ValueError", "value": "bad login for email=ivan@example.com"}]
        }
    }
    result = _scrub(event, "BASIC")
    value = result["exception"]["values"][0]["value"]
    assert "ivan@example.com" not in value
    assert "i***@example.com" in value


def test_scrubs_url_valued_referer_header_dict() -> None:
    event = {
        "request": {
            "headers": {
                "Referer": "https://app.test/search?email=alice@example.com&page=2",
                "Accept": "application/json",
            }
        }
    }
    result = _scrub(event, "BASIC")
    referer = result["request"]["headers"]["Referer"]
    assert "alice@example.com" not in referer and "alice%40example.com" not in referer
    assert "page=2" in referer
    assert result["request"]["headers"]["Accept"] == "application/json"


def test_scrubs_url_valued_referer_header_list() -> None:
    event = {
        "request": {
            "headers": [
                ["Referer", "https://app.test/x?email=bob@example.com"],
                ["Accept", "text/html"],
            ]
        }
    }
    result = _scrub(event, "BASIC")
    headers = dict(result["request"]["headers"])
    assert "bob@example.com" not in headers["Referer"]
    assert headers["Accept"] == "text/html"


def test_scrub_url_headers_writes_back_tuple_pair() -> None:
    # Regression: a URL header arriving as a ``(name, value)`` *tuple* pair must
    # still be scrubbed. The old code copied the tuple to a fresh local list and
    # mutated that, silently discarding the scrub and leaking the Referer query.
    from observe_kit.pii_rules import PiiLevel
    from observe_kit.sentry.config import _build_opts, _scrub_url_headers

    opts, _, _ = _build_opts(PiiLevel.SENSITIVE, "pepper", None, None, None)
    headers = [("Referer", "https://h/p?email=alice@example.com")]
    _scrub_url_headers(headers, opts)
    assert "alice@example.com" not in headers[0][1]
    assert "alice%40example.com" not in headers[0][1]


def test_scrub_url_values_writes_back_tuple_pair() -> None:
    # Regression: a custom URL-valued header as a ``(name, value)`` tuple pair
    # used to raise ``TypeError`` on the ``obj[i][1] = ...`` assignment, aborting
    # before_send and dropping the whole event. It must scrub in place instead.
    from observe_kit.pii_rules import PiiLevel
    from observe_kit.sentry.config import _build_opts, _scrub_url_values

    opts, _, _ = _build_opts(PiiLevel.SENSITIVE, "pepper", None, None, None)
    headers = [("X-Next", "/search?phone=0812345678")]
    _scrub_url_values(headers, opts)  # must not raise
    assert "0812345678" not in headers[0][1]


def test_scrubs_url_valued_referer_in_cgi_env() -> None:
    event = {"request": {"env": {"HTTP_REFERER": "https://app.test/x?email=carol@example.com"}}}
    result = _scrub(event, "BASIC")
    assert "carol@example.com" not in result["request"]["env"]["HTTP_REFERER"]


def test_redacts_db_query_text_and_parameters() -> None:
    # Current OTel DB semantic conventions: db.query.text + db.query.parameter.*
    event = {
        "spans": [
            {
                "op": "db",
                "data": {
                    "db.query.text": "SELECT * FROM users WHERE email = 'judy@example.com'",
                    "db.query.parameter.email": "judy@example.com",
                    "db.query.parameter.0": "judy@example.com",
                },
            }
        ]
    }
    result = _scrub(event, "BASIC")
    data = result["spans"][0]["data"]
    assert data["db.query.text"] == "[Filtered]"
    assert data["db.query.parameter.email"] == "[Filtered]"
    assert data["db.query.parameter.0"] == "[Filtered]"


def test_redacts_db_parameter_with_nonstring_value() -> None:
    # An IN-clause / array bind parameter is a list, not a string — it must be
    # redacted wholesale by key, not recursed into (where phone would leak).
    event = {"spans": [{"op": "db", "data": {"db.query.parameter.phones": ["0812345678", "0899"]}}]}
    result = _scrub(event, "BASIC")
    assert result["spans"][0]["data"]["db.query.parameter.phones"] == "[Filtered]"


def test_redacts_db_parameter_nonstring_value_in_pair_list() -> None:
    # Same as above but in the list-of-pairs span-data form: a non-string
    # statement bind value must be redacted by key, not recursed into.
    event = {"spans": [{"op": "db.query", "data": [["db.query.parameter.phones", ["0812345678"]]]}]}
    result = _scrub(event, "BASIC")
    assert result["spans"][0]["data"] == [["db.query.parameter.phones", "[Filtered]"]]


def test_redacts_db_statement_in_span_data() -> None:
    # db.statement carries SQL literals that can't be parsed out — redact it
    # wholesale, matching the db/cache span *description* treatment.
    event = {
        "spans": [
            {
                "op": "db",
                "data": {"db.statement": "SELECT * FROM users WHERE email = 'judy@example.com'"},
            }
        ]
    }
    result = _scrub(event, "BASIC")
    assert result["spans"][0]["data"]["db.statement"] == "[Filtered]"


def test_scrubs_breadcrumb_message_text() -> None:
    event = {
        "breadcrumbs": {"values": [{"category": "log", "message": "sent to dave@example.com"}]}
    }
    result = _scrub(event, "BASIC")
    assert result["breadcrumbs"]["values"][0]["message"] == "sent to d***@example.com"


def test_scrubs_breadcrumb_data() -> None:
    event = {
        "breadcrumbs": {
            "values": [
                {"category": "http", "data": {"email": "erin@example.com"}},
                {"category": "log", "message": "hi"},
            ]
        }
    }
    result = _scrub(event, "BASIC")
    assert result["breadcrumbs"]["values"][0]["data"]["email"] == "e***@example.com"


def test_scrubs_breadcrumb_list_form() -> None:
    event = {"breadcrumbs": [{"data": {"email": "frank@example.com"}}]}
    result = _scrub(event, "BASIC")
    assert result["breadcrumbs"][0]["data"]["email"] == "f***@example.com"


def test_scrubs_url_field_in_breadcrumb_data() -> None:
    # HTTP breadcrumbs carry a full URL under data["url"] whose query has PII.
    event = {
        "breadcrumbs": {
            "values": [
                {
                    "category": "http",
                    "data": {
                        "url": "https://app.test/search?email=alice@example.com&page=2",
                        "method": "GET",
                    },
                }
            ]
        }
    }
    result = _scrub(event, "BASIC")
    url = result["breadcrumbs"]["values"][0]["data"]["url"]
    assert "alice@example.com" not in url and "alice%40example.com" not in url
    assert "page=2" in url
    assert result["breadcrumbs"]["values"][0]["data"]["method"] == "GET"


def test_scrubs_nested_url_field_in_extra() -> None:
    event = {"extra": {"context": {"url": "https://app.test/x?email=bob@example.com"}}}
    result = _scrub(event, "BASIC")
    assert "bob@example.com" not in result["extra"]["context"]["url"]


def test_cookies_redacted_wholesale_dict() -> None:
    # A session/auth cookie under a non-standard key must not pass through.
    event = {"request": {"cookies": {"my_session": "raw-secret-value"}}}
    result = _scrub(event, "BASIC")
    assert result["request"]["cookies"] == "[Filtered]"


def test_cookies_redacted_wholesale_raw_string() -> None:
    # Non-dict cookie shapes (raw Cookie header / list) are also redacted.
    event = {"request": {"cookies": "sessionid=abc; auth=Bearer x"}}
    result = _scrub(event, "BASIC")
    assert result["request"]["cookies"] == "[Filtered]"


def test_redacts_query_like_request_fragment() -> None:
    # A standalone request.fragment carrying an OAuth token / query must be
    # redacted like the fragment component of request.url; a plain anchor is kept.
    tok = _scrub({"request": {"fragment": "access_token=supersecret"}}, "BASIC")
    assert tok["request"]["fragment"] == "[Filtered]"
    anchor = _scrub({"request": {"fragment": "section-2"}}, "BASIC")
    assert anchor["request"]["fragment"] == "section-2"


def test_redacts_multiply_encoded_request_fragment() -> None:
    event = {"request": {"fragment": "access_token%253Dsupersecret"}}
    result = _scrub(event, "BASIC")
    assert result["request"]["fragment"] == "[Filtered]"


def test_duplicate_query_params_preserved() -> None:
    event = {"request": {"query_string": "tag=a&tag=b&email=x@y.com"}}
    result = _scrub(event, "BASIC")
    qs = result["request"]["query_string"]
    assert qs.count("tag=a") == 1 and qs.count("tag=b") == 1  # both survive
    assert "x@y.com" not in qs and "x%40y.com" not in qs  # email masked


def test_extra_mask_fields_applied() -> None:
    # Operator-defined EXTRA_MASK_FIELDS must scrub custom keys in body/extra.
    extra_mask = frozenset({"ssn"})
    event = {"request": {"data": {"ssn": "123-45-6789"}}, "extra": {"ssn": "123-45-6789"}}
    result = _scrub(event, "BASIC", extra_mask=extra_mask)
    assert result["request"]["data"]["ssn"].endswith("***")
    assert result["extra"]["ssn"].endswith("***")


def test_scrubs_user_email() -> None:
    # user.email is in MASK_FIELDS and must be masked at BASIC.
    event = {"user": {"id": "7", "email": "ivan@example.com", "username": "ivan"}}
    result = _scrub(event, "BASIC")
    assert result["user"]["email"] == "i***@example.com"
    assert result["user"]["id"] == "7"


def test_sensitive_masks_user_email_and_hashes_ip() -> None:
    event = {"user": {"email": "jane@example.com", "ip_address": "203.0.113.9"}}
    result = _scrub(event, "SENSITIVE")
    assert result["user"]["email"] == "j***@example.com"  # masked
    assert result["user"]["ip_address"] != "203.0.113.9"  # hashed
    assert len(result["user"]["ip_address"]) == 64


def test_scrubs_query_in_request_url() -> None:
    event = {"request": {"url": "https://app.test/search?email=alice@example.com&page=2#frag"}}
    result = _scrub(event, "BASIC")
    url = result["request"]["url"]
    assert "alice@example.com" not in url and "alice%40example.com" not in url
    assert url.startswith("https://app.test/search?")
    assert "page=2" in url
    assert url.endswith("#frag")  # plain anchor fragment preserved


def test_url_field_honors_explicit_mask_over_url_scrub() -> None:
    # A non-email secret path segment survives _scrub_url; an explicit
    # EXTRA_MASK_FIELDS={"url"} must redact the whole request.url instead
    # of passing through _mask_value (which preserves the suffix after @).
    event = {"request": {"url": "https://app.test/reset/supersecret-token"}}
    result = _scrub(event, "BASIC", extra_mask=frozenset({"url"}))
    assert result["request"]["url"] == "[Filtered]"


def test_url_field_explicit_mask_redacts_path_emails() -> None:
    # An explicit mask on a URL key redacts the whole value — path emails
    # are removed as a side effect.
    event = {"request": {"url": "https://app.test/alice@example.com/bob@example.com"}}
    result = _scrub(event, "BASIC", extra_mask=frozenset({"url"}))
    assert result["request"]["url"] == "[Filtered]"


def test_url_field_honors_explicit_drop() -> None:
    event = {"request": {"url": "https://app.test/reset/supersecret-token"}}
    result = _scrub(event, "BASIC", extra_drop=frozenset({"url"}))
    assert result["request"]["url"] == "[Filtered]"  # dropped/redacted, not scrubbed


def test_redacts_url_fragment_with_token() -> None:
    # OAuth implicit-flow tokens land in the fragment.
    event = {"request": {"url": "https://app.test/callback#access_token=secret&token_type=bearer"}}
    result = _scrub(event, "BASIC")
    url = result["request"]["url"]
    assert "secret" not in url
    assert url == "https://app.test/callback#[Filtered]"


def test_redacts_url_fragment_with_email() -> None:
    event = {
        "spans": [{"op": "http.client", "data": {"http.url": "https://app.test/x#email=a@b.co"}}]
    }
    result = _scrub(event, "BASIC")
    assert "a@b.co" not in result["spans"][0]["data"]["http.url"]


def test_url_without_query_unchanged() -> None:
    event = {"request": {"url": "https://app.test/health"}}
    result = _scrub(event, "BASIC")
    assert result["request"]["url"] == "https://app.test/health"


def test_malformed_url_with_query_redacted_not_raising() -> None:
    # urlsplit raises ValueError here; before_send must not propagate it.
    event = {"request": {"url": "http://[/p?email=alice@example.com"}}
    result = _scrub(event, "BASIC")
    url = result["request"]["url"]
    assert "alice@example.com" not in url
    assert url == "http://[/p?[Filtered]"


def test_malformed_url_without_query_unchanged() -> None:
    event = {"request": {"url": "http://["}}
    result = _scrub(event, "BASIC")
    assert result["request"]["url"] == "http://["


def test_malformed_url_with_userinfo_redacted_wholesale() -> None:
    # urlsplit raises here; the fallback must not leak the user:secret creds.
    for bad in ("https://user:secret@[", "https://user:secret@[?email=alice@example.com"):
        result = _scrub({"request": {"url": bad}}, "BASIC")
        url = result["request"]["url"]
        assert url == "[Filtered]"
        assert "secret" not in url and "alice@example.com" not in url


def test_malformed_protocol_relative_url_userinfo_redacted() -> None:
    # Scheme-relative //authority form: the fallback must still catch userinfo.
    for bad in ("//user:secret@[", "//user:secret@[?email=alice@example.com"):
        url = _scrub({"request": {"url": bad}}, "BASIC")["request"]["url"]
        assert url == "[Filtered]"
        assert "secret" not in url and "alice@example.com" not in url


def test_malformed_url_fragment_token_redacted_in_fallback() -> None:
    # urlsplit raises; a token fragment with no '?' must still be redacted.
    result = _scrub({"request": {"url": "http://[/cb#access_token=leak"}}, "BASIC")
    url = result["request"]["url"]
    assert "access_token=leak" not in url and "leak" not in url
    assert url == "http://[/cb#[Filtered]"


def test_malformed_url_plain_fragment_kept_in_fallback() -> None:
    # A plain anchor on an unparseable URL is preserved (no '=' or '@').
    result = _scrub({"request": {"url": "http://[/p#section"}}, "BASIC")
    assert result["request"]["url"] == "http://[/p#section"


def test_malformed_url_path_email_masked_in_fallback() -> None:
    # urlsplit raises on the authority; the surviving base path must not leak
    # its email (literal or %40-encoded), even as the query is redacted.
    for bad in ("http://[/users/alice@example.com?x=1", "http://[/users/alice%40example.com"):
        url = _scrub({"request": {"url": bad}}, "BASIC")["request"]["url"]
        assert "alice@example.com" not in url and "alice%40example.com" not in url
        assert "a***@example.com" in url


def test_scrubs_url_userinfo_credentials() -> None:
    # basic-auth creds in the authority must be stripped, query still scrubbed.
    event = {"request": {"url": "https://user:secret@api.test/search?email=alice@example.com"}}
    result = _scrub(event, "BASIC")
    url = result["request"]["url"]
    assert "user:secret" not in url and "secret" not in url
    assert "api.test/search" in url  # host + path preserved
    assert "alice@example.com" not in url  # query still scrubbed


def test_scrubs_url_userinfo_without_query() -> None:
    # userinfo stripped even when there's no query component.
    event = {"spans": [{"op": "http.client", "data": {"http.url": "https://u:p@api.test/x"}}]}
    result = _scrub(event, "BASIC")
    url = result["spans"][0]["data"]["http.url"]
    assert "u:p" not in url and "@api.test/x" in url


def test_masks_email_in_url_path() -> None:
    # PII can sit in the path (not just the query): /users/alice@example.com
    event = {"request": {"url": "https://app.test/users/alice@example.com"}}
    result = _scrub(event, "BASIC")
    url = result["request"]["url"]
    assert "alice@example.com" not in url
    assert "a***@example.com" in url
    assert url.startswith("https://app.test/users/")


def test_masks_percent_encoded_email_in_url_path() -> None:
    # urlsplit leaves %40 encoded; the raw email is still recoverable.
    event = {"request": {"url": "https://app.test/users/alice%40example.com/profile"}}
    result = _scrub(event, "BASIC")
    url = result["request"]["url"]
    assert "alice%40example.com" not in url and "alice@example.com" not in url
    assert "a***@example.com" in url


def test_redacts_percent_encoded_email_fragment() -> None:
    # #alice%40example.com has no literal '=' or '@' until decoded.
    event = {"request": {"url": "https://app.test/x#alice%40example.com"}}
    result = _scrub(event, "BASIC")
    url = result["request"]["url"]
    assert "alice%40example.com" not in url and "alice@example.com" not in url


def test_redacts_multiply_encoded_url_fragment_token() -> None:
    event = {"request": {"url": "https://example.test/cb#access_token%253Dsupersecret"}}
    result = _scrub(event, "BASIC")
    url = result["request"]["url"]
    assert "supersecret" not in url
    assert url == "https://example.test/cb#[Filtered]"


def test_masks_internationalized_email_in_freetext() -> None:
    # The email backstop must recognize Unicode/internationalized addresses
    # in both the local part and the domain (EAI — Email Address Internationalization).
    event = {"extra": {"msg": "contact alice@例え.テスト for details"}}
    result = _scrub(event, "BASIC")
    msg = result["extra"]["msg"]
    assert "alice@例え.テスト" not in msg
    assert "a***@例え.テスト" in msg

    # Unicode local part with ASCII domain.
    event2 = {"extra": {"msg": "contact 用户@example.com"}}
    result2 = _scrub(event2, "BASIC")
    assert "用户@example.com" not in result2["extra"]["msg"]
    assert "***@example.com" in result2["extra"]["msg"]


def test_scrubs_http_target_span_query_and_path() -> None:
    # http.target is a path+query emitted by this repo's OTel middleware.
    event = {
        "spans": [
            {"op": "http.server", "data": {"http.target": "/search?email=alice@example.com"}},
            {"op": "http.server", "data": {"http.target": "/users/bob@example.com"}},
        ]
    }
    result = _scrub(event, "BASIC")
    assert "alice@example.com" not in result["spans"][0]["data"]["http.target"]
    assert "bob@example.com" not in result["spans"][1]["data"]["http.target"]


def test_scrubs_url_path_span_key_percent_encoded() -> None:
    # url.path is a bare path emitted from request.path; a %40-encoded email in
    # it must be decoded and masked like http.target / request.url, not left raw.
    event = {
        "spans": [
            {"op": "http.server", "data": {"url.path": "/users/alice%40example.com/profile"}},
            {"op": "http.server", "data": {"url.path": "/users/bob@example.com"}},
        ]
    }
    result = _scrub(event, "BASIC")
    first = result["spans"][0]["data"]["url.path"]
    assert "alice%40example.com" not in first and "alice@example.com" not in first
    assert "a***@example.com" in first
    assert "bob@example.com" not in result["spans"][1]["data"]["url.path"]


def test_scrubs_http_path_tag_percent_encoded() -> None:
    # http.path is a tag set by SentryContextMiddleware from request.path; a
    # %40-encoded email must be decoded + masked in both the dict and list forms.
    dict_evt = _scrub({"tags": {"http.path": "/users/alice%40example.com"}}, "BASIC")
    tag = dict_evt["tags"]["http.path"]
    assert "alice%40example.com" not in tag and "alice@example.com" not in tag
    assert "a***@example.com" in tag
    list_evt = _scrub({"tags": [["http.path", "/users/bob%40example.com"]]}, "BASIC")
    assert "bob%40example.com" not in dict(list_evt["tags"])["http.path"]


def test_scrubs_url_in_http_span_description() -> None:
    event = {
        "spans": [
            {
                "op": "http.client",
                "description": "GET https://api.test/search?email=alice@example.com",
            }
        ]
    }
    result = _scrub(event, "BASIC")
    desc = result["spans"][0]["description"]
    assert "alice@example.com" not in desc and "alice%40example.com" not in desc
    assert desc.startswith("GET https://api.test/search?")


def test_db_span_description_redacted_wholesale() -> None:
    event = {
        "spans": [
            {"op": "db", "description": "SELECT * FROM users WHERE email = 'alice@example.com'"}
        ]
    }
    result = _scrub(event, "BASIC")
    assert result["spans"][0]["description"] == "[Filtered]"


def test_plain_span_description_unchanged() -> None:
    event = {"spans": [{"op": "function", "description": "render homepage"}]}
    result = _scrub(event, "BASIC")
    assert result["spans"][0]["description"] == "render homepage"


def test_scrubs_email_in_non_db_span_description() -> None:
    # Arbitrary custom span text can embed PII with no URL to anchor on.
    event = {"spans": [{"op": "function", "description": "processing alice@example.com"}]}
    result = _scrub(event, "BASIC")
    desc = result["spans"][0]["description"]
    assert "alice@example.com" not in desc
    assert desc == "processing a***@example.com"


def test_span_description_honors_explicit_field_rule() -> None:
    # An operator rule targeting "description" wins over the free-text scrub — a
    # non-email secret (token=…) would otherwise survive in a non-db span.
    mask = _scrub(
        {"spans": [{"op": "custom", "description": "token=supersecret"}]},
        "BASIC",
        extra_mask=frozenset({"description"}),
    )
    assert "supersecret" not in mask["spans"][0]["description"]

    drop = _scrub(
        {"spans": [{"op": "custom", "description": "token=supersecret"}]},
        "BASIC",
        extra_drop=frozenset({"description"}),
    )
    assert drop["spans"][0]["description"] == "[Filtered]"


def test_scrubs_transaction_spans_and_name() -> None:
    event = {
        "type": "transaction",
        "transaction": "/search?email=alice@example.com",
        "spans": [
            {"op": "http.client", "data": {"url": "https://api.test/u?email=bob@example.com"}},
            {"op": "db", "data": {"email": "carol@example.com"}},
        ],
    }
    result = _scrub(event, "BASIC")
    assert "alice@example.com" not in result["transaction"]
    assert "bob@example.com" not in result["spans"][0]["data"]["url"]
    assert result["spans"][1]["data"]["email"] == "c***@example.com"


def test_transaction_field_honors_explicit_mask() -> None:
    # A secret path segment has no query/email for _scrub_url to rewrite; an
    # explicit EXTRA_MASK_FIELDS={"transaction"} must mask the whole name.
    event = {"transaction": "/reset/supersecret-token"}
    result = _scrub(event, "BASIC", extra_mask=frozenset({"transaction"}))
    assert result["transaction"] == "[Filtered]"


def test_transaction_field_honors_explicit_drop() -> None:
    event = {"transaction": "/reset/supersecret-token"}
    result = _scrub(event, "BASIC", extra_drop=frozenset({"transaction"}))
    assert "transaction" not in result  # dropped, not URL-scrubbed


def test_scrubs_stack_local_vars() -> None:
    event = {
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "stacktrace": {
                        "frames": [{"function": "f", "vars": {"email": "dave@example.com", "n": 1}}]
                    },
                }
            ]
        }
    }
    result = _scrub(event, "BASIC")
    frame_vars = result["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]
    assert frame_vars["email"] == "d***@example.com"
    assert frame_vars["n"] == 1


def test_before_send_transaction_is_wired() -> None:
    # init_sentry must install before_send_transaction so trace events are scrubbed.
    from unittest.mock import patch

    from observe_kit.sentry.config import init_sentry

    with patch("observe_kit.sentry.config.sentry_sdk.init") as mock_init:
        init_sentry(dsn="https://k@sentry.io/1", environment="test")
    kwargs = mock_init.call_args.kwargs
    assert callable(kwargs.get("before_send_transaction"))
    # The wired scrubber must actually scrub a transaction event.
    txn = {"transaction": "/x?email=alice@example.com", "spans": []}
    scrubbed = kwargs["before_send_transaction"](txn, None)
    assert "alice@example.com" not in scrubbed["transaction"]


def test_sensitive_hashes_ip_fields() -> None:
    event = {
        "request": {"env": {"REMOTE_ADDR": "203.0.113.7"}},
        "user": {"ip_address": "203.0.113.7", "id": "42"},
    }
    result = _scrub(event, "SENSITIVE")
    hashed_env = result["request"]["env"]["REMOTE_ADDR"]
    hashed_user = result["user"]["ip_address"]
    assert hashed_env != "203.0.113.7"
    assert len(hashed_env) == 64  # sha256 hex
    assert hashed_user == hashed_env  # same value + salt -> same hash
    assert result["user"]["id"] == "42"  # untouched


def test_sensitive_hashes_forwarded_ip_headers_dict() -> None:
    event = {
        "request": {"headers": {"X-Forwarded-For": "203.0.113.7", "Accept": "application/json"}}
    }
    result = _scrub(event, "SENSITIVE")
    headers = result["request"]["headers"]
    assert headers["X-Forwarded-For"] != "203.0.113.7"
    assert len(headers["X-Forwarded-For"]) == 64  # hashed
    assert headers["Accept"] == "application/json"


def test_sensitive_hashes_forwarded_ip_headers_list() -> None:
    event = {"request": {"headers": [["X-Real-IP", "203.0.113.7"], ["Accept", "text/html"]]}}
    result = _scrub(event, "SENSITIVE")
    headers = dict(result["request"]["headers"])
    assert headers["X-Real-IP"] != "203.0.113.7" and len(headers["X-Real-IP"]) == 64
    assert headers["Accept"] == "text/html"


def test_basic_does_not_hash_forwarded_ip_header() -> None:
    event = {"request": {"headers": {"X-Forwarded-For": "203.0.113.7"}}}
    result = _scrub(event, "BASIC")
    assert result["request"]["headers"]["X-Forwarded-For"] == "203.0.113.7"


def test_scrubs_cgi_env_http_headers_and_query() -> None:
    # request.env populated from WSGI META: HTTP_* headers + raw QUERY_STRING.
    event = {
        "request": {
            "env": {
                "HTTP_AUTHORIZATION": "Bearer secret",
                "HTTP_COOKIE": "sessionid=abc123",
                "HTTP_ACCEPT": "application/json",
                "QUERY_STRING": "email=alice@example.com&page=2",
                "SERVER_NAME": "app.test",
            }
        }
    }
    result = _scrub(event, "BASIC")
    env = result["request"]["env"]
    assert "HTTP_AUTHORIZATION" not in env  # dropped
    assert "HTTP_COOKIE" not in env  # dropped
    assert env["HTTP_ACCEPT"] == "application/json"  # non-sensitive header kept
    qs = env["QUERY_STRING"]
    assert "alice@example.com" not in qs and "alice%40example.com" not in qs
    assert "page=2" in qs
    assert env["SERVER_NAME"] == "app.test"  # non-HTTP_ metadata untouched


def test_redacts_csrf_cookie_env_secret() -> None:
    # Django's CSRF middleware exposes the raw secret as META["CSRF_COOKIE"]; it
    # isn't in the built-in drop set, so it must be redacted wholesale like a
    # cookie rather than shipped raw.
    event = {"request": {"env": {"CSRF_COOKIE": "supersecretcsrftoken", "SERVER_NAME": "app.test"}}}
    result = _scrub(event, "BASIC")
    env = result["request"]["env"]
    assert env["CSRF_COOKIE"] == "[Filtered]"
    assert "supersecretcsrftoken" not in str(env)
    assert env["SERVER_NAME"] == "app.test"  # non-cookie metadata untouched


def test_sensitive_hashes_cgi_env_forwarded_ip_and_user_agent() -> None:
    event = {
        "request": {
            "env": {
                "REMOTE_ADDR": "203.0.113.7",
                "HTTP_X_FORWARDED_FOR": "203.0.113.7",
                "HTTP_USER_AGENT": "curl/8.0",
            }
        }
    }
    result = _scrub(event, "SENSITIVE")
    env = result["request"]["env"]
    assert env["REMOTE_ADDR"] != "203.0.113.7" and len(env["REMOTE_ADDR"]) == 64
    # forwarded IP header (CGI form) hashed to the same value as REMOTE_ADDR
    assert env["HTTP_X_FORWARDED_FOR"] == env["REMOTE_ADDR"]
    # user-agent is a HASH_FIELD, hashed via the header rules (once, not doubled)
    assert env["HTTP_USER_AGENT"] != "curl/8.0" and len(env["HTTP_USER_AGENT"]) == 64


def test_basic_does_not_hash_cgi_env_ip() -> None:
    event = {
        "request": {"env": {"REMOTE_ADDR": "203.0.113.7", "HTTP_X_FORWARDED_FOR": "203.0.113.7"}}
    }
    result = _scrub(event, "BASIC")
    env = result["request"]["env"]
    assert env["REMOTE_ADDR"] == "203.0.113.7"
    assert env["HTTP_X_FORWARDED_FOR"] == "203.0.113.7"


def test_scrubs_uri_mirror_env_entries() -> None:
    # REQUEST_URI / RAW_URI / PATH_INFO mirror the request URL's path+query.
    event = {
        "request": {
            "env": {
                "REQUEST_URI": "/search?email=alice@example.com",
                "RAW_URI": "/search?email=alice@example.com",
                "PATH_INFO": "/users/bob@example.com",
            }
        }
    }
    result = _scrub(event, "BASIC")
    env = result["request"]["env"]
    assert "alice@example.com" not in env["REQUEST_URI"]
    assert "alice@example.com" not in env["RAW_URI"]
    assert "bob@example.com" not in env["PATH_INFO"]


def test_env_honors_extra_drop_for_uri_mirror_key() -> None:
    # An operator that drops path_info must win over the URL scrub — a secret in a
    # non-email path segment would otherwise survive because the URL scrub only
    # masks emails/query, not arbitrary path segments.
    event = {"request": {"env": {"PATH_INFO": "/reset/supersecret-token"}}}
    result = _scrub(event, "BASIC", extra_drop=frozenset({"path_info"}))
    assert "PATH_INFO" not in result["request"]["env"]  # dropped, not URL-scrubbed


def test_env_honors_extra_mask_for_uri_mirror_key() -> None:
    # EXTRA_MASK_FIELDS={"request_uri"} must mask the whole value rather than only
    # scrubbing its query/email components.
    event = {"request": {"env": {"REQUEST_URI": "/reset/supersecret-token?x=1"}}}
    result = _scrub(event, "BASIC", extra_mask=frozenset({"request_uri"}))
    uri = result["request"]["env"]["REQUEST_URI"]
    assert uri == "[Filtered]"


def test_env_honors_extra_drop_for_query_string_key() -> None:
    # An operator that drops query_string must win over the query scrub.
    event = {"request": {"env": {"QUERY_STRING": "token=supersecret&page=2"}}}
    result = _scrub(event, "BASIC", extra_drop=frozenset({"query_string"}))
    assert "QUERY_STRING" not in result["request"]["env"]


def test_env_uri_mirror_still_url_scrubbed_without_operator_rule() -> None:
    # With no operator rule for the key, the URL/query scrub still runs (regression
    # guard so the operator-first change doesn't disable the default scrub).
    event = {"request": {"env": {"PATH_INFO": "/users/bob@example.com"}}}
    result = _scrub(event, "BASIC", extra_drop=frozenset({"unrelated_key"}))
    assert "bob@example.com" not in result["request"]["env"]["PATH_INFO"]


def test_env_honors_extra_rules_for_non_http_keys() -> None:
    # Non-HTTP_ WSGI keys configured in EXTRA_MASK_FIELDS / EXTRA_DROP_HEADERS
    # must be masked/dropped by key name; unconfigured keys are left alone.
    event = {
        "request": {
            "env": {"REMOTE_USER": "operator", "SESSION_KEY": "sekrit", "SERVER_NAME": "app.test"}
        }
    }
    result = _scrub(
        event, "BASIC", extra_mask=frozenset({"remote_user"}), extra_drop=frozenset({"session_key"})
    )
    env = result["request"]["env"]
    assert env["REMOTE_USER"].endswith("***")  # masked by EXTRA_MASK_FIELDS
    assert "SESSION_KEY" not in env  # dropped by EXTRA_DROP_HEADERS
    assert env["SERVER_NAME"] == "app.test"  # unconfigured, untouched


def test_env_honors_explicit_drop_for_remote_addr() -> None:
    # An operator that drops remote_addr must win over the SENSITIVE IP hashing.
    event = {"request": {"env": {"REMOTE_ADDR": "203.0.113.7"}}}
    basic = _scrub(event, "BASIC", extra_drop=frozenset({"remote_addr"}))
    assert "REMOTE_ADDR" not in basic["request"]["env"]
    sensitive = _scrub(
        {"request": {"env": {"REMOTE_ADDR": "203.0.113.7"}}},
        "SENSITIVE",
        extra_drop=frozenset({"remote_addr"}),
    )
    assert "REMOTE_ADDR" not in sensitive["request"]["env"]  # dropped, not hashed


def test_env_honors_explicit_mask_for_remote_addr_at_basic() -> None:
    event = {"request": {"env": {"REMOTE_ADDR": "203.0.113.7"}}}
    result = _scrub(event, "BASIC", extra_mask=frozenset({"remote_addr"}))
    addr = result["request"]["env"]["REMOTE_ADDR"]
    assert addr != "203.0.113.7" and addr.endswith("***")


def test_env_remote_addr_honors_ip_alias_rules() -> None:
    # The semantic alias "ip" targets REMOTE_ADDR too (not only "remote_addr").
    drop = _scrub(
        {"request": {"env": {"REMOTE_ADDR": "203.0.113.7"}}},
        "SENSITIVE",
        extra_drop=frozenset({"ip"}),
    )
    assert "REMOTE_ADDR" not in drop["request"]["env"]  # dropped, not hashed

    mask = _scrub(
        {"request": {"env": {"REMOTE_ADDR": "203.0.113.7"}}},
        "SENSITIVE",
        extra_mask=frozenset({"ip"}),
    )
    addr = mask["request"]["env"]["REMOTE_ADDR"]
    assert addr.endswith("***") and len(addr) != 64  # masked, not hashed


def test_ip_alias_masks_user_ip_address() -> None:
    # The semantic alias "ip" must apply to user.ip_address too, not only to
    # env.REMOTE_ADDR — EXTRA_MASK_FIELDS/HASH/DROP keyed by "ip" covers both.
    drop = _scrub(
        {"user": {"ip_address": "203.0.113.7", "email": "a@b.com"}},
        "SENSITIVE",
        extra_drop=frozenset({"ip"}),
    )
    assert "ip_address" not in drop["user"]  # dropped via alias
    assert drop["user"]["email"] == "a***@b.com"  # other fields still scrubbed

    mask_basic = _scrub(
        {"user": {"ip_address": "203.0.113.7"}}, "BASIC", extra_mask=frozenset({"ip"})
    )
    assert mask_basic["user"]["ip_address"].endswith("***")

    # At SENSITIVE, the mask wins over the IP hash (masked value is not hex).
    mask_sensitive = _scrub(
        {"user": {"ip_address": "203.0.113.7"}}, "SENSITIVE", extra_mask=frozenset({"ip"})
    )
    ip = mask_sensitive["user"]["ip_address"]
    assert ip.endswith("***") and len(ip) != 64


def test_ip_alias_drops_proxy_ip_headers() -> None:
    # The semantic "ip" alias must apply to forwarded IP headers too, not only
    # to env.REMOTE_ADDR — EXTRA_DROP_HEADERS keyed by "ip" drops them all.
    drop = _scrub(
        {
            "request": {
                "headers": {"X-Forwarded-For": "203.0.113.7", "X-Real-IP": "10.0.0.1"},
                "env": {"HTTP_X_FORWARDED_FOR": "203.0.113.7"},
            }
        },
        "SENSITIVE",
        extra_drop=frozenset({"ip"}),
    )
    assert "X-Forwarded-For" not in drop["request"]["headers"]
    assert "X-Real-IP" not in drop["request"]["headers"]
    assert "HTTP_X_FORWARDED_FOR" not in drop["request"]["env"]

    # mask via "ip" alias: the masked value must not be a hash hex string
    # (mask wins over the SENSITIVE IP hash).
    mask = _scrub(
        {"request": {"headers": {"X-Forwarded-For": "203.0.113.7"}}},
        "SENSITIVE",
        extra_mask=frozenset({"ip"}),
    )
    masked_val = mask["request"]["headers"]["X-Forwarded-For"]
    assert masked_val.endswith("***") and len(masked_val) != 64


def test_explicit_mask_wins_over_ip_hash_at_sensitive() -> None:
    # An explicit EXTRA_MASK for a client-IP field must survive SENSITIVE IP
    # hashing (otherwise Sentry gets sha256 of the masked string, not the mask)
    # — REMOTE_ADDR, forwarded IP header, and user.ip_address all covered.
    event = {
        "request": {
            "env": {"REMOTE_ADDR": "203.0.113.7", "HTTP_X_FORWARDED_FOR": "203.0.113.7"},
            "headers": {"X-Forwarded-For": "203.0.113.7"},
        },
        "user": {"ip_address": "203.0.113.7"},
    }
    result = _scrub(
        event, "SENSITIVE", extra_mask=frozenset({"remote_addr", "x-forwarded-for", "ip_address"})
    )
    env = result["request"]["env"]
    assert env["REMOTE_ADDR"].endswith("***") and len(env["REMOTE_ADDR"]) != 64
    assert env["HTTP_X_FORWARDED_FOR"].endswith("***")
    assert result["request"]["headers"]["X-Forwarded-For"].endswith("***")
    assert result["user"]["ip_address"].endswith("***")


def test_ip_still_hashed_at_sensitive_without_explicit_mask() -> None:
    # Regression: with no explicit mask, the client IP is still hashed.
    event = {
        "request": {"env": {"REMOTE_ADDR": "203.0.113.7"}},
        "user": {"ip_address": "203.0.113.7"},
    }
    result = _scrub(event, "SENSITIVE")
    assert len(result["request"]["env"]["REMOTE_ADDR"]) == 64
    assert result["user"]["ip_address"] == result["request"]["env"]["REMOTE_ADDR"]


def test_ip_address_not_double_hashed_with_extra_hash() -> None:
    # EXTRA_HASH_FIELDS containing ip_address must not double-hash user.ip_address:
    # it stays a single sha256 that still equals the REMOTE_ADDR hash.
    event = {
        "request": {"env": {"REMOTE_ADDR": "203.0.113.7"}},
        "user": {"ip_address": "203.0.113.7"},
    }
    result = _scrub(event, "SENSITIVE", extra_hash=frozenset({"ip_address"}))
    assert result["user"]["ip_address"] == result["request"]["env"]["REMOTE_ADDR"]
    assert len(result["user"]["ip_address"]) == 64


def test_forwarded_ip_header_not_double_hashed_with_extra_hash() -> None:
    # EXTRA_HASH_FIELDS containing a forwarded-IP header must not double-hash it:
    # the header/env mapping pass must skip it so _hash_ip_fields hashes once and
    # the digest still equals REMOTE_ADDR's.
    event = {
        "request": {
            "headers": {"X-Forwarded-For": "203.0.113.7"},
            "env": {"REMOTE_ADDR": "203.0.113.7", "HTTP_X_FORWARDED_FOR": "203.0.113.7"},
        }
    }
    result = _scrub(event, "SENSITIVE", extra_hash=frozenset({"x-forwarded-for"}))
    remote = result["request"]["env"]["REMOTE_ADDR"]
    assert len(remote) == 64
    assert result["request"]["headers"]["X-Forwarded-For"] == remote  # single hash, matches
    assert result["request"]["env"]["HTTP_X_FORWARDED_FOR"] == remote


def test_basic_does_not_hash_ip() -> None:
    event = {
        "request": {"env": {"REMOTE_ADDR": "203.0.113.7"}},
        "user": {"ip_address": "203.0.113.7"},
    }
    result = _scrub(event, "BASIC")
    assert result["request"]["env"]["REMOTE_ADDR"] == "203.0.113.7"
    assert result["user"]["ip_address"] == "203.0.113.7"


def test_none_level_is_noop() -> None:
    event = {"request": {"data": {"email": "x@example.com"}}, "extra": {"email": "y@example.com"}}
    result = _scrub(event, "NONE")
    assert result["request"]["data"]["email"] == "x@example.com"
    assert result["extra"]["email"] == "y@example.com"


def test_masked_email_key_collision_preserves_entries() -> None:
    event = {"extra": {"alice@example.com": "one", "a***@example.com": "two"}}
    result = _scrub(event, "BASIC")
    extra = result["extra"]
    assert "alice@example.com" not in extra
    assert len(extra) == 2
    assert sorted(extra.values()) == ["one", "two"]


def test_prescrubbed_request_key_collision_preserves_entries() -> None:
    event = {"request": {"query_string": {"alice@example.com": "one", "a***@example.com": "two"}}}
    result = _scrub(event, "BASIC")
    query_string = result["request"]["query_string"]
    assert "alice@example.com" not in query_string
    assert len(query_string) == 2
    assert sorted(query_string.values()) == ["one", "two"]


def test_log_params_key_collision_preserves_entries() -> None:
    event = {"logentry": {"params": {"alice@example.com": "one", "a***@example.com": "two"}}}
    result = _scrub(event, "BASIC")
    params = result["logentry"]["params"]
    assert "alice@example.com" not in params
    assert len(params) == 2
    assert sorted(params.values()) == ["one", "two"]


def test_malformed_non_string_keys_do_not_raise() -> None:
    event = {"extra": {1: "alice@example.com"}}
    result = _scrub(event, "BASIC")
    assert result["extra"][1] == "a***@example.com"


def test_malformed_env_non_string_keys_do_not_raise_at_sensitive() -> None:
    event = {"request": {"env": {1: "203.0.113.7"}}}
    result = _scrub(event, "SENSITIVE")
    assert result["request"]["env"][1] == "203.0.113.7"


def test_malformed_non_string_keys_do_not_raise_in_log_params() -> None:
    event = {"logentry": {"params": {1: "alice@example.com"}}}
    result = _scrub(event, "BASIC")
    assert result["logentry"]["params"][1] == "a***@example.com"


def test_logentry_params_pair_list_redacts_statement_key() -> None:
    event = {"logentry": {"params": [["db.statement", "SELECT phone=0812345678"]]}}
    result = _scrub(event, "BASIC")
    assert result["logentry"]["params"] == [["db.statement", "[Filtered]"]]


def test_sensitive_non_string_env_key_does_not_raise_during_ip_hashing() -> None:
    event = {"request": {"env": {1: "203.0.113.7", "REMOTE_ADDR": "203.0.113.8"}}}
    result = _scrub(event, "SENSITIVE")
    assert result["request"]["env"][1] == "203.0.113.7"
    assert len(result["request"]["env"]["REMOTE_ADDR"]) == 64


def test_masked_url_does_not_leak_path_via_at_authority() -> None:
    # When EXTRA_MASK_FIELDS targets a URL key, _mask_value must not preserve
    # the path after @ in the authority (``https://user:pass@host/secret`` →
    # ``[Filtered]``, not ``h***@host/secret``).
    event = {"request": {"url": "https://alice:password@host/reset/supersecret"}}
    result = _scrub(event, "BASIC", extra_mask=frozenset({"url"}))
    assert result["request"]["url"] == "[Filtered]"

    # A URL in extra under a ``url`` key is also safely redacted.
    extra_evt = {"extra": {"url": "https://alice:password@host/admin/secret"}}
    extra_result = _scrub(extra_evt, "BASIC", extra_mask=frozenset({"url"}))
    assert extra_result["extra"]["url"] == "[Filtered]"


def test_extra_hash_on_url_key_does_not_leak_path() -> None:
    # When EXTRA_HASH_FIELDS targets a URL key, the value must be hashed — not
    # corrupted by _mask_value's ``@`` preservation.
    event = {"request": {"url": "https://alice:password@host/reset/supersecret"}}
    result = _scrub(event, "SENSITIVE", extra_hash=frozenset({"url"}))
    assert len(result["request"]["url"]) == 64  # sha256 hex digest


def test_masked_query_string_list_values_leak_no_secret() -> None:
    # When query_string is a dict with list values (repeated params) and a key
    # is masked, each element must be masked individually — not stringified
    # into a repr that leaks surviving elements past _mask_value.
    event = {"request": {"query_string": {"token": ["x@y", "supersecret"]}}}
    result = _scrub(event, "BASIC", extra_mask=frozenset({"token"}))
    qs = result["request"]["query_string"]
    assert "supersecret" not in str(qs)
    assert "x@y" not in str(qs)

    # Also covers the case where no mask rule applies (URL scrubbing still works).
    safe_evt = {"request": {"query_string": {"tag": ["a", "b"]}}}
    safe_result = _scrub(safe_evt, "BASIC")
    assert safe_result["request"]["query_string"]["tag"] == ["a", "b"]


def test_redacts_deeply_encoded_email_key_at_decode_cap() -> None:
    # A dict key that is a six-times percent-encoded email (``%25`` repeated
    # such that after the decode budget ``@`` has not yet emerged) must not
    # bypass the email backstop — the original encoded key is recoverable
    # by a single additional decode and would expose the email structure.
    event = {"extra": {"alice%252525252540example.com": "x"}}
    result = _scrub(event, "BASIC")
    extra = result["extra"]
    # The encoded email key should be redacted (``[Filtered]``) or absent,
    # not preserved in its recoverable encoded form.
    assert "alice%252525252540example.com" not in str(extra)
    assert "alice" not in str(extra) or "[Filtered]" in str(extra)


def test_scrubs_encoded_url_keyed_value_with_hidden_query() -> None:
    # When a URL-keyed value has percent-encoded URL delimiters (``%2F`` for ``/``,
    # ``%3F`` for ``?``), it must be decoded before ``urlsplit`` so the query
    # component is visible for structural scrubbing.
    event = {"request": {"url": "%2Fsearch%3Fphone%3D0812345678"}}
    result = _scrub(event, "BASIC")
    url = result["request"]["url"]
    assert "0812345678" not in url

    # Also works for a URL-keyed value with encoded credentials in the authority.
    event2 = {"extra": {"http.url": "https%3A%2F%2Falice%3Asecret%40host%2Fdb"}}
    result2 = _scrub(event2, "BASIC")
    url2 = result2["extra"]["http.url"]
    assert "alice" not in url2 and "secret" not in url2


def test_deeply_encoded_url_path_email_masked_by_backstop() -> None:
    # A deeply percent-encoded email in a URL path (``%2525252540`` →
    # 4 decode passes before ``%40`` emerges) is masked by the email backstop
    # rather than leaking through the path.
    event = {"request": {"url": "https://host/u/alice%2525252540example.com"}}
    result = _scrub(event, "BASIC")
    url = result["request"]["url"]
    assert "alice" not in url


def test_url_field_decode_cap_exhausted_redacts_value() -> None:
    """When ``_bounded_unquote`` exhausts its decode cap on a URL value, redact."""
    from urllib.parse import quote

    nested = "/search?phone=0812345678"
    for _ in range(7):
        nested = quote(nested, safe="")
    event = {"request": {"url": nested}}
    result = _scrub(event, "BASIC")
    assert result["request"]["url"] == "[Filtered]"


def test_url_field_preserves_non_pii_path_escapes() -> None:
    """Preserve non-PII path escapes (``%2F``, ``%20``) that carry no hidden query."""
    event = {"request": {"url": "https://host/api/v1/a%2Fb%20test"}}
    result = _scrub(event, "BASIC")
    url = result["request"]["url"]
    assert url == "https://host/api/v1/a%2Fb%20test"


def test_scrubs_encoded_url_delimiters_in_referer_header() -> None:
    """Bounded-decode URL-valued headers so hidden query PII is exposed."""
    event = {"request": {"headers": {"Referer": "https://host/search%3Fphone%3D0812345678"}}}
    result = _scrub(event, "BASIC")
    referer = result["request"]["headers"]["Referer"]
    assert "0812345678" not in referer


def test_scrubs_encoded_url_delimiters_in_cgi_referer() -> None:
    """Same fix for CGI-normalised URL-valued headers in request.env."""
    event = {"request": {"env": {"HTTP_REFERER": "https://host/path%3Femail%3Dalice@example.com"}}}
    result = _scrub(event, "BASIC")
    ref = result["request"]["env"]["HTTP_REFERER"]
    assert "alice" not in ref
    assert "alice@example.com" not in ref


def test_scrubs_encoded_url_delimiters_in_referer_env_exhausted() -> None:
    """When decode cap is exhausted on a URL-valued header, redact."""
    from urllib.parse import quote

    encoded = "https://host/search%3Fphone%3D0812345678"
    for _ in range(5):
        encoded = quote(encoded, safe="")
    event = {"request": {"env": {"HTTP_REFERER": encoded}}}
    result = _scrub(event, "BASIC")
    assert result["request"]["env"]["HTTP_REFERER"] == "[Filtered]"


def test_url_header_exposes_hidden_query_but_keeps_path_escapes() -> None:
    """A URL mixing ordinary path escapes with an encoded query delimiter must
    expose/scrub the hidden query while retaining the unrelated ``%2F`` escape."""
    event = {"request": {"headers": {"Referer": "https://host/a%2Fb%3Fphone%3D0812345678"}}}
    result = _scrub(event, "BASIC")
    referer = result["request"]["headers"]["Referer"]
    assert "0812345678" not in referer
    assert "a%2Fb" in referer  # path segment kept its escape, not split into a/b


def test_masks_quoted_local_part_with_embedded_at() -> None:
    """A quoted local part carrying its own ``@`` is masked at the final
    separator so no part of the local identity is recoverable."""
    event = {"extra": {"note": 'contact "alice@dept"@example.com now'}}
    result = _scrub(event, "BASIC")
    note = result["extra"]["note"]
    assert "alice@dept" not in note and "dept" not in note


def test_url_header_keeps_value_internal_encoded_ampersand() -> None:
    """A deeply encoded ``&`` (``%2526``) inside a param value must stay encoded,
    not be promoted to a separator that splits one param into two, while the
    outer query delimiter is still exposed."""
    event = {"request": {"headers": {"Referer": "https://host/path%3Fq%3Da%2526b"}}}
    result = _scrub(event, "BASIC")
    referer = result["request"]["headers"]["Referer"]
    assert referer == "https://host/path?q=a%26b"  # one param q=a&b, not q=a & b=


def test_url_header_exposes_outer_depth_ampersand_separator() -> None:
    """An outer-depth ``%26`` between two params is still exposed as a separator."""
    event = {
        "request": {
            "headers": {"Referer": "https://host/p%3Femail%3Dalice%40example.com%26page%3D2"}
        }
    }
    result = _scrub(event, "BASIC")
    referer = result["request"]["headers"]["Referer"]
    assert "alice@example.com" not in referer and "alice%40example.com" not in referer
    assert "page=2" in referer  # second param preserved as its own param


def test_url_header_scrubs_hidden_query_beside_visible_outer_query() -> None:
    """An encoded query hidden in the path must be scrubbed even when the URL
    already carries a harmless visible outer query (``?x=1``)."""
    for target in (
        {"request": {"url": "https://host/search%3Fphone%3D0812345678?x=1"}},
        {"request": {"headers": {"Referer": "https://host/search%3Fphone%3D0812345678?x=1"}}},
    ):
        result = _scrub(dict(target), "BASIC")
        assert "0812345678" not in str(result)


def test_masks_unquoted_local_part_specials() -> None:
    """Atext specials (``!``, ``'``, ``$`` …) in an unquoted local part must be
    masked, not left as a visible prefix before a masked suffix."""
    for addr, leaked in (
        ("alice!dept@example.com", "alice!dept"),
        ("o'hara@example.com", "o'hara"),
        ("a$b@example.com", "a$b"),
    ):
        note = _scrub({"extra": {"note": f"contact {addr}"}}, "BASIC")["extra"]["note"]
        assert leaked not in note


def test_email_regex_does_not_corrupt_url_path_or_query() -> None:
    """The expanded local-part class must not swallow URL path segments or query
    structure — ``/`` and ``=``/``?`` stay excluded."""
    path_evt = _scrub({"request": {"url": "https://host/users/alice@example.com"}}, "BASIC")
    assert "/users/" in path_evt["request"]["url"]
    msg_evt = _scrub({"message": "email=alice@example.com"}, "BASIC")
    assert "alice@example.com" not in msg_evt["message"]
    assert msg_evt["message"].startswith("email=")  # key not swallowed


def test_raw_query_mask_redacts_partial_email_wholesale() -> None:
    """A masked raw query value whose email is followed by a secret must be
    redacted wholesale, not emitted as a partial ``a***@domain secret`` mask."""
    for target in (
        {"request": {"query_string": "phone=alice%40example.com+supersecret"}},
        {"request": {"url": "https://h/x?phone=alice%40example.com+supersecret"}},
        {"request": {"data": "phone=alice%40example.com+supersecret"}},
    ):
        result = _scrub(dict(target), "BASIC", extra_mask=frozenset({"phone"}))
        assert "supersecret" not in str(result)


def test_custom_url_header_mask_redacts_wholesale() -> None:
    """A masked custom URL-carrying header must be redacted wholesale so the
    partial mask can't leave a recoverable path secret; a plain masked value
    (an IP) keeps its ordinary partial mask."""
    leak = _scrub(
        {"request": {"headers": {"X-Next": "https://alice:password@host/reset/supersecret"}}},
        "BASIC",
        extra_mask=frozenset({"x-next"}),
    )
    assert "supersecret" not in str(leak) and "password" not in str(leak)
    ip = _scrub(
        {"request": {"headers": {"X-Forwarded-For": "203.0.113.7"}}},
        "SENSITIVE",
        extra_mask=frozenset({"ip"}),
    )
    assert ip["request"]["headers"]["X-Forwarded-For"].endswith("***")


def test_hidden_query_beside_outer_query_preserves_outer() -> None:
    """Exposing a path-carried query must keep the already-parsed outer query."""
    result = _scrub({"request": {"url": "https://host/search%3Fphone%3D0812345678?x=1"}}, "BASIC")
    url = result["request"]["url"]
    assert "0812345678" not in url and "x=1" in url


def test_fully_encoded_url_authority_credentials_redacted() -> None:
    """A fully double-percent-encoded URL whose ``://`` and ``@`` are encoded must
    have its ``user:pass@`` authority redacted, not decoded and exposed."""
    encoded = "https%253A%252F%252Falice%253Asecret%2540host%252Fdb"
    for target in (
        {"request": {"url": encoded}},
        {"request": {"headers": {"Referer": encoded}}},
        {"extra": {"http.url": encoded}},
    ):
        result = _scrub(dict(target), "BASIC")
        assert "secret" not in str(result) and "alice:secret" not in str(result)


def test_scrubs_encoded_relative_url_after_colon_prefix() -> None:
    """An encoded relative URL following a ``redirect:`` prefix must be scrubbed,
    not restored, while the prefix is preserved."""
    result = _scrub({"message": "redirect:%252Fsearch%253Fphone%253D0812345678"}, "BASIC")
    msg = result["message"]
    assert "0812345678" not in msg
    assert msg.startswith("redirect:")


def test_hidden_encoded_url_preserves_surrounding_encoded_text() -> None:
    """Only the hidden encoded-URL slice is scrubbed; unrelated encoded slices
    around it (``hello%20world``, ``progress=100%25``) keep their encoding."""
    result = _scrub(
        {"message": "%252Fsearch%253Fphone%253D0812345678,hello%20world progress=100%25"}, "BASIC"
    )
    msg = result["message"]
    assert "0812345678" not in msg
    assert "hello%20world" in msg and "progress=100%25" in msg


def test_absolute_url_stops_at_prose_punctuation() -> None:
    """A visible absolute URL followed by comma-delimited prose must be isolated
    from it, not swallow the prose into the masked query value."""
    result = _scrub({"message": "see https://host/p?phone=0812345678,progress=100%25"}, "BASIC")
    msg = result["message"]
    assert "0812345678" not in msg
    assert "progress=100%25" in msg  # trailing prose preserved, not masked away


def test_malformed_event_does_not_raise() -> None:
    # before_send must never raise; odd shapes are tolerated.
    for event in ({}, {"request": "not-a-dict"}, {"breadcrumbs": 5}, {"extra": "str"}, {"user": 1}):
        assert _scrub(dict(event), "SENSITIVE") is not None


def test_masks_email_with_domain_literal() -> None:
    # An RFC 5321 domain-literal address (a bracketed IPv4/IPv6 host, e.g.
    # ``alice@[192.0.2.1]`` / ``bob@[IPv6:2001:db8::1]``) carries no dotted TLD,
    # so the dotted-domain email pattern misses it and the (PII) local part would
    # reach Sentry unchanged in messages, extras, and other generic leaves. The
    # whole-event backstop must mask these too, at BASIC and SENSITIVE.
    for level in ("BASIC", "SENSITIVE"):
        event = {
            "message": "contact alice@[192.0.2.1] now",
            "extra": {"who": "bob@[IPv6:2001:db8::1]"},
        }
        result = _scrub(event, level)
        assert "alice@[192.0.2.1]" not in result["message"]
        assert "a***@[192.0.2.1]" in result["message"]
        who = result["extra"]["who"]
        assert who == "b***@[IPv6:2001:db8::1]" and "bob@" not in who


def test_preserves_benign_double_encoded_percent() -> None:
    # A doubly-encoded percent (``%2525``) with no email must survive: one decode
    # leaves ``%25``, and treating that alone as evidence of a hidden email
    # redacted benign, context-bearing values. Bounded decoding reveals no email
    # indicator, so the value is kept verbatim.
    event = {"extra": {"note": "progress=100%2525", "blob": "abc%2525def"}}
    result = _scrub(event, "BASIC")
    assert result["extra"]["note"] == "progress=100%2525"
    assert result["extra"]["blob"] == "abc%2525def"


def test_still_redacts_double_encoded_percent_hiding_email() -> None:
    # The benign-percent fix must not weaken redaction: a value whose doubly
    # encoded octets actually decode to an email is still scrubbed — its (PII)
    # local part masked, the encoded form gone (domain kept, per email masking).
    event = {"extra": {"blob": "user%252540example.com"}}
    result = _scrub(event, "BASIC")
    blob = result["extra"]["blob"]
    assert blob == "u***@example.com"
    assert "user" not in blob and "%2540" not in blob and "%40" not in blob.lower()


def test_scrubs_percent_encoded_uri_env_mirror() -> None:
    # REQUEST_URI / RAW_URI can arrive percent-encoded (``%2F``/``%3F``/``%3D``),
    # hiding the query delimiters from urlsplit. A direct _scrub_url sees no query
    # and ships the phone raw; the mirror must be bounded-decoded (as URL
    # headers/fields are) so the inner phone (a MASK field) is scrubbed.
    event = {
        "request": {
            "env": {
                "REQUEST_URI": "%2Fsearch%3Fphone%3D0812345678",
                "RAW_URI": "/search?phone=0812345678",
            }
        }
    }
    result = _scrub(event, "BASIC")
    env = result["request"]["env"]
    assert "0812345678" not in env["REQUEST_URI"]
    assert "0812345678" not in env["RAW_URI"]


def test_scrubs_encoded_query_hidden_in_already_visible_url() -> None:
    # A URL that is already visible (``https://…``) can still hide its query
    # delimiters in the path via encoding: after ``parse_qsl`` decodes ``next``
    # once, ``https://h/p%3Fphone%3D0812345678`` reaches the scrubber and the
    # ``%3Fphone%3D…`` would be mistaken for path text. Exposure now happens
    # inside ``_scrub_url`` itself, so every surface is covered uniformly.
    qs = _scrub(
        {"request": {"query_string": "next=https%3A%2F%2Fh%2Fp%253Fphone%253D0812345678"}}, "BASIC"
    )["request"]["query_string"]
    assert "0812345678" not in qs

    # A generic (non-URL-key) event leaf carrying the same visible URL.
    leaf = _scrub({"extra": {"u": "https://h/p%3Fphone%3D0812345678"}}, "BASIC")["extra"]["u"]
    assert "0812345678" not in leaf
    assert leaf.startswith("https://h/p?phone=")  # query exposed and masked

    # A non-allowlisted URL-carrying request header (handled by _scrub_url_values).
    hdr = _scrub({"request": {"headers": {"X-Next": "https://h/p%3Fphone%3D0812345678"}}}, "BASIC")[
        "request"
    ]["headers"]["X-Next"]
    assert "0812345678" not in hdr


def test_scrubs_encoded_fragment_hidden_in_embedded_url() -> None:
    # An embedded URL that hides its fragment delimiter (``%23``) would otherwise
    # have ``%23access_token%3D…`` treated as path text, leaking the OAuth token.
    # ``_scrub_url`` exposes the fragment before parsing and redacts it.
    message = _scrub({"message": "visit https://h/callback%23access_token%3Dsupersecret"}, "BASIC")[
        "message"
    ]
    assert "supersecret" not in message
    assert message.startswith("visit https://h/callback")  # visible prefix preserved


def test_redacts_compound_masked_body_value_wholesale() -> None:
    # A masked body field whose value is an email followed by an arbitrary secret
    # must be redacted wholesale — ``_mask_value`` alone keeps everything after
    # the email domain (``a***@example.com recovery-code=supersecret``). The fix
    # lives in the shared ``_mask_value`` so ``request.data``, ``extra``,
    # contexts, and breadcrumb/span bodies are all covered.
    data = _scrub(
        {"request": {"data": {"email": "alice@example.com recovery-code=supersecret"}}}, "BASIC"
    )["request"]["data"]
    assert "supersecret" not in str(data)
    extra = _scrub({"extra": {"email": "alice@example.com token=supersecret"}}, "BASIC")["extra"]
    assert "supersecret" not in str(extra)
    # A bare email under a masked key still keeps its debuggable domain mask.
    bare = _scrub({"request": {"data": {"email": "bob@example.com"}}}, "BASIC")["request"]["data"]
    assert bare["email"] == "b***@example.com"


def test_scrubs_relative_callback_fragment() -> None:
    # A relative callback URL with a query-like ``#access_token=…`` fragment is a
    # URL-carrying value even without a ``?`` query; it must route through
    # ``_scrub_url`` so the OAuth token in the fragment is redacted. Covers the
    # absolute-URL query value, the raw query_string, and free text.
    url = _scrub(
        {"request": {"url": "https://host/start?next=%2Fcallback%23access_token%3Dsupersecret"}},
        "BASIC",
    )["request"]["url"]
    assert "supersecret" not in url

    qs = _scrub(
        {"request": {"query_string": "next=%2Fcallback%23access_token%3Dsupersecret"}}, "BASIC"
    )["request"]["query_string"]
    assert "supersecret" not in qs

    message = _scrub({"message": "go to /callback#access_token=supersecret now"}, "BASIC")[
        "message"
    ]
    assert "supersecret" not in message


def test_deeply_nested_redirect_does_not_recurse_unbounded() -> None:
    # A maliciously nested ``next=/p?next=/p?…`` must not blow the Python stack
    # inside ``before_send`` (which would drop the event). The URL-scrub recursion
    # is bounded and the value is redacted once the budget is exhausted.
    nested = "x"
    for _ in range(300):
        nested = "/p?next=" + nested
    result = _scrub({"request": {"query_string": "next=" + nested}}, "BASIC")
    qs = result["request"]["query_string"]
    assert "Filtered" in qs  # redacted rather than raising RecursionError

    # A legitimately shallow nested redirect is still scrubbed, not redacted.
    shallow = _scrub(
        {"request": {"query_string": "next=/go?next=/search%3Fphone%3D0812345678"}}, "BASIC"
    )["request"]["query_string"]
    assert "0812345678" not in shallow


def test_scrubs_bytes_valued_leaves() -> None:
    # Sentry serializes bytes/bytearray leaves to strings *after* before_send, so
    # a byte-valued leaf must be decoded and scrubbed like a string — otherwise
    # ``set_extra("note", b"alice@example.com")`` reaches Sentry as the raw
    # address. Covers extra / contexts / tags / message / request body / query
    # pairs / a bytes URL-cred value / a bytes dict key.
    result = _scrub(
        {
            "message": b"from alice@example.com",
            "extra": {"note": b"alice@example.com", "u": b"https://user:secret@host/db"},
            "contexts": {"app": {"note": bytearray(b"contact bob@example.com")}},
            "tags": {"t": b"carol@example.com"},
            "request": {
                "data": {"note": b"dave@example.com"},
                "query_string": [["q", b"eve@example.com"]],
            },
        },
        "BASIC",
    )
    flat = str(result)
    for leaked in (
        "alice@example.com",
        "bob@example.com",
        "carol@example.com",
        "dave@example.com",
        "eve@example.com",
        "secret",
    ):
        assert leaked not in flat, leaked
    assert result["extra"]["note"] == "a***@example.com"  # decoded + masked
    assert isinstance(result["message"], str)  # normalized to str, not bytes

    # A bytes dict key carrying an email is decoded and masked too.
    key_result = _scrub({"extra": {b"alice@example.com": 1}}, "BASIC")["extra"]
    assert "alice@example.com" not in str(key_result)


def test_masks_email_with_single_label_domain() -> None:
    # ``alice@localhost`` / ``alice@mailserver`` are valid single-label-domain
    # addresses (RFC 5321) common in dev/test data; the dotted-domain-only
    # branch of the email regex left them raw in generic leaves.
    msg = _scrub({"message": "contact alice@localhost please"}, "BASIC")["message"]
    assert "alice@localhost" not in msg and "***" in msg

    extra = _scrub({"extra": {"cc": "bob@mailserver"}}, "BASIC")["extra"]["cc"]
    assert "bob@mailserver" not in extra


def test_url_token_stops_at_quote_and_angle_brackets() -> None:
    # Quoted / angle-bracketed URLs must not consume their closing delimiter:
    # ``visit "https://…?phone=…" now`` used to drop the closing quote.
    msg = _scrub({"message": 'visit "https://host/p?phone=0812345678" now'}, "BASIC")["message"]
    assert "0812345678" not in msg
    assert '" now' in msg  # closing quote preserved

    angle = _scrub({"message": "<https://host/p?phone=0812345678> done"}, "BASIC")["message"]
    assert "0812345678" not in angle
    assert "> done" in angle


def test_comma_inside_query_still_scrubbed_but_prose_preserved() -> None:
    # A comma is valid inside a URI query value; the URL token now continues
    # past a comma when it is followed by ``&``-joined query structure, so a
    # sensitive param after the comma isn't left raw.
    msg = _scrub({"message": "see https://host/p?x=a,b&phone=0812345678 now"}, "BASIC")["message"]
    assert "0812345678" not in msg

    # But comma-delimited *prose* after a URL is still kept out of the URL.
    prose = _scrub({"message": "https://host/p?phone=123,progress=100%25 done"}, "BASIC")["message"]
    assert "progress=100%25" in prose


def test_encoded_userinfo_preserves_path_escapes() -> None:
    # Partially encoded userinfo (``alice%3Asecret%40host``) must be redacted
    # without flattening the path: ``a%2Fb`` stays a single encoded segment.
    msg = _scrub({"message": "https://alice%3Asecret%40host/a%2Fb"}, "BASIC")["message"]
    assert "secret" not in msg
    assert "[Filtered]@host/a%2Fb" in msg
    assert "/a/b" not in msg


def test_scrubs_rootless_relative_urls_in_free_text() -> None:
    # A valid rootless relative reference (no leading ``/``) that carries a
    # query or query-like fragment must have its PII scrubbed.
    query = _scrub({"message": "redirect callback?phone=0812345678"}, "BASIC")["message"]
    assert "0812345678" not in query

    frag = _scrub({"message": "redirect callback#access_token=supersecret"}, "BASIC")["message"]
    assert "supersecret" not in frag


def test_unwraps_serialized_byte_keys() -> None:
    # Sentry serializes a nested byte key (``b"phone"``) to its repr string
    # (``"b'phone'"``) before before_send; the field rule must still match.
    extra = _scrub({"extra": {"b'phone'": "0812345678"}}, "BASIC")["extra"]
    assert "0812345678" not in str(extra)
    assert "phone" in extra

    # When a plain key already exists alongside the repr, both entries are kept
    # distinct rather than silently clobbering one another — and the repr key's
    # value is still field-ruled via its unwrapped name.
    both = _scrub({"extra": {"phone": "0812345678", "b'phone'": "0999999999"}}, "BASIC")["extra"]
    assert "0812345678" not in str(both)
    assert "0999999999" not in str(both)  # masked, not leaked
    assert "phone" in both and "b'phone'" in both


def test_field_rules_apply_to_colliding_bytes_repr_keys() -> None:
    # A serialized byte key kept distinct from its plain form must still match
    # the operator field rules and the walk-managed keys by its unwrapped name —
    # otherwise ``extra={"authorization": …, "b'authorization'": "Bearer x"}``
    # drops only the plain key and ships the credential raw.
    drop = _scrub(
        {"extra": {"authorization": "safe", "b'authorization'": "Bearer supersecret"}}, "BASIC"
    )["extra"]
    assert "Bearer supersecret" not in str(drop)
    assert "safe" not in str(drop)

    walk_url = _scrub({"extra": {"url": "/safe", "b'url'": "/search?phone=0812345678"}}, "BASIC")[
        "extra"
    ]
    assert "0812345678" not in str(walk_url)

    statement = _scrub({"extra": {"b'db.statement'": "SELECT phone FROM users"}}, "BASIC")["extra"]
    assert "SELECT" not in str(statement)


def test_preserves_benign_encoded_urls_in_free_text() -> None:
    # An encoded URL whose decoded parameters are not sensitive is kept as-is —
    # not redacted to ``[Filtered]`` — matching how its visible equivalent is
    # preserved.
    msg = _scrub({"message": "redirect %2Fdocs%3Fpage%3D1"}, "BASIC")["message"]
    assert "[Filtered]" not in msg
    assert "%2Fdocs%3Fpage%3D1" in msg

    # A sensitive encoded URL is still redacted.
    leaky = _scrub({"message": "redirect %2Fsearch%3Fphone%3D0812345678"}, "BASIC")["message"]
    assert "0812345678" not in leaky


def test_span_tags_honor_field_rules() -> None:
    # ``span.set_tag("phone", …)`` serializes into ``spans[*].tags``; the
    # drop/mask/hash field rules must apply there too (dict and pair-list
    # shapes), not just to span ``data``.
    dict_span = _scrub({"spans": [{"tags": {"phone": "0812345678"}}]}, "BASIC")
    assert "0812345678" not in str(dict_span["spans"][0]["tags"])

    pair_span = _scrub({"spans": [{"tags": [["authorization", "Bearer x"]]}]}, "BASIC")["spans"][0][
        "tags"
    ]
    assert pair_span == []  # authorization dropped

    drop_span = _scrub({"spans": [{"tags": {"ssn": "123-45-6789"}}]}, "BASIC", extra_drop={"ssn"})[
        "spans"
    ][0]["tags"]
    assert "ssn" not in drop_span


def test_scrubs_rootless_relative_urls_with_slashes() -> None:
    # A rootless relative reference can span multiple path segments
    # (``account/callback?phone=…``) and carry a query or query-like fragment.
    query = _scrub({"message": "redirect account/callback?phone=0812345678"}, "BASIC")["message"]
    assert "0812345678" not in query

    frag = _scrub({"message": "redirect account/callback#access_token=supersecret"}, "BASIC")[
        "message"
    ]
    assert "supersecret" not in frag


def test_keeps_query_subdelims_inside_embedded_urls() -> None:
    # ``;`` is a valid URI sub-delimiter inside a query value; a sensitive param
    # after it (joined by ``&``) must still be scrubbed, while prose after the
    # URL is preserved.
    msg = _scrub({"message": "see https://host/p?x=a;b&phone=0812345678 now"}, "BASIC")["message"]
    assert "0812345678" not in msg

    prose = _scrub({"message": "https://host/p?phone=123; done"}, "BASIC")["message"]
    assert "; done" in prose


def test_scrubs_encoded_rootless_fragments_in_free_text() -> None:
    # An encoded ``#`` (``%23``) hiding a rootless query-like fragment must be
    # exposed and its token redacted.
    msg = _scrub({"message": "redirect callback%23access_token%3Dsupersecret"}, "BASIC")["message"]
    assert "supersecret" not in msg
    assert "#[Filtered]" in msg
