from django.http import JsonResponse
from django.urls import include, path


def home(_request):
    return JsonResponse(
        {
            "service": "example-grafana-metrics",
            "endpoints": ["/api/quotes/quote/", "/metrics"],
        }
    )


urlpatterns = [
    path("", home),
    path("api/", include("demo_api.urls")),
    path("", include("observe_kit.urls")),
]
