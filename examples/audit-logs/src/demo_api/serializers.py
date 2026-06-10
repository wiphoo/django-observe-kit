from rest_framework import serializers

from observe_kit.audit.models import AuditLog


class QuoteItemSerializer(serializers.Serializer):
    sku = serializers.CharField(max_length=64)
    quantity = serializers.IntegerField(min_value=1)
    unit_price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)


class QuoteRequestSerializer(serializers.Serializer):
    customer_id = serializers.CharField(max_length=64)
    items = QuoteItemSerializer(many=True, min_length=1)


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = [
            "id",
            "timestamp",
            "action",
            "object_type",
            "object_id",
            "tenant_id",
            "trace_id",
            "remote_addr",
            "user_agent",
            "extra",
        ]
