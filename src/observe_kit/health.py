from django.http import HttpResponse


def healthz(request):  # pragma: no cover - trivial view
    return HttpResponse("ok", content_type="text/plain")
