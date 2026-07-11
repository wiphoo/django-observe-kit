from rest_framework import serializers


class PrivacyPayloadSerializer(serializers.Serializer):
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=32)
    ssn = serializers.CharField(max_length=32)
    session_id = serializers.CharField(max_length=128)
