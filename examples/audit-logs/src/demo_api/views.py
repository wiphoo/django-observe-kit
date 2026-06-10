from __future__ import annotations

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from observe_kit.audit.models import AuditLog
from observe_kit.context import get_request_context

from .serializers import AuditLogSerializer, QuoteRequestSerializer
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

        context = get_request_context()
        return Response(
            {
                "quote": quote,
                "observability": {
                    "route": context.route,
                    "trace_id": context.trace_id,
                    "span_id": context.span_id,
                },
            },
            status=status.HTTP_200_OK,
        )


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AuditLog.objects.all()
    serializer_class = AuditLogSerializer
