from observe_kit.context import RequestContext, get_request_context, reset_request_context


def test_request_context_round_trip():
    reset_request_context()
    context = get_request_context()
    context.method = "GET"
    context.path = "/healthz"
    again = get_request_context()
    assert again.method == "GET"
    assert again.path == "/healthz"
    assert isinstance(again, RequestContext)
