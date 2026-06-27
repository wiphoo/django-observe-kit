from observe_kit.context import RequestContext, get_request_context, reset_request_context


def test_request_context_round_trip() -> None:
    reset_request_context()
    context = get_request_context()
    context.method = "GET"
    context.path = "/metrics"
    again = get_request_context()
    assert again.method == "GET"
    assert again.path == "/metrics"
    assert isinstance(again, RequestContext)
