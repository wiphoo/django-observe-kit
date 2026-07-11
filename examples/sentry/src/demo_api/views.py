from __future__ import annotations

import sentry_sdk
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response


class DemoViewSet(viewsets.ViewSet):
    @action(detail=False, methods=["get"])
    def failure(self, _request):
        error = RuntimeError("Intentional Sentry demo failure")
        sentry_sdk.capture_exception(error)
        return Response({"detail": "Server Error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
