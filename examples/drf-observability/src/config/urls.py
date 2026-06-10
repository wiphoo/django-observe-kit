from django.http import JsonResponse
from django.urls import include, path


def home(_request):
    return JsonResponse(
        {
            "service": "example-drf-observability",
            "endpoints": ["/api/quotes/quote/", "/api/quotes/failure/", "/metrics"],
        }
    )


urlpatterns = [
    path("", home),
    path("api/", include("api.urls")),
    path("", include("observe_kit.urls")),
]
