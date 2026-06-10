from __future__ import annotations

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .serializers import QuoteRequestSerializer
from .services import build_quote


class QuoteViewSet(viewsets.ViewSet):
    @action(detail=False, methods=["post"])
    def quote(self, request):
        serializer = QuoteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        payload = serializer.validated_data
        quote = build_quote(
            customer_id=payload["customer_id"],
            items=list(payload["items"]),
            request=request,
        )

        return Response(
            {
                "quote": quote,
            },
            status=status.HTTP_200_OK,
        )
