from __future__ import annotations

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from observe_kit.audit import audit
from observe_kit.context import get_request_context

from .serializers import PrivacyPayloadSerializer


class PrivacyViewSet(viewsets.ViewSet):
    @action(detail=False, methods=["post"])
    def submit(self, request):
        serializer = PrivacyPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = dict(serializer.validated_data)
        audit(action="privacy_payload_submitted", extra=payload, request=request)
        context = get_request_context()

        return Response(
            {
                "stored": True,
                "context": {
                    "query_params": dict(context.query_params),
                    "trace_id": context.trace_id,
                    "route": context.route,
                },
            },
            status=status.HTTP_200_OK,
        )
