from __future__ import annotations

from decimal import Decimal

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from observe_kit.context import get_request_context

from .serializers import QuoteRequestSerializer


class QuoteViewSet(viewsets.ViewSet):
    @action(detail=False, methods=["post"])
    def quote(self, request):
        serializer = QuoteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        subtotal = sum(
            Decimal(str(item["unit_price"])) * Decimal(item["quantity"])
            for item in payload["items"]
        )
        context = get_request_context()

        return Response(
            {
                "quote": {"subtotal": f"{subtotal:.2f}"},
                "observability": {
                    "trace_id": context.trace_id,
                    "route": context.route,
                },
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"])
    def failure(self, _request):
        raise RuntimeError("Intentional DRF observability demo failure")
