from rest_framework import serializers


class QuoteItemSerializer(serializers.Serializer):
    sku = serializers.CharField(max_length=64)
    quantity = serializers.IntegerField(min_value=1)
    unit_price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)


class QuoteRequestSerializer(serializers.Serializer):
    customer_id = serializers.CharField(max_length=64)
    items = QuoteItemSerializer(many=True, min_length=1)
