from django.http import JsonResponse
from django.urls import include, path


def home(_request):
    return JsonResponse({"service": "example-metrics-security"})


urlpatterns = [
    path("", home),
    path("", include("observe_kit.urls")),
]
