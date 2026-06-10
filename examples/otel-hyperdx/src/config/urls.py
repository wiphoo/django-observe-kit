from django.http import JsonResponse
from django.urls import include, path


def home(_request):
    return JsonResponse(
        {
            "service": "example-otel-hyperdx",
            "endpoints": ["/api/quotes/quote/"],
        }
    )


urlpatterns = [
    path("", home),
    path("api/", include("demo_api.urls")),
]
