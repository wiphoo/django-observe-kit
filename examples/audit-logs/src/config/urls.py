from django.http import JsonResponse
from django.urls import include, path


def home(_request):
    return JsonResponse(
        {
            "service": "example-audit-logs",
            "endpoints": ["/api/quotes/quote/", "/api/audit/"],
        }
    )


urlpatterns = [
    path("", home),
    path("api/", include("demo_api.urls")),
]
