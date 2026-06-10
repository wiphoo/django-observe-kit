from django.http import JsonResponse
from django.urls import include, path


def home(_request):
    return JsonResponse({"service": "example-pii-sanitization"})


urlpatterns = [
    path("", home),
    path("api/", include("privacy.urls")),
    path("", include("observe_kit.urls")),
]
