"""DRF serializers for API."""

from django.contrib.auth.models import User
from rest_framework import serializers

from .models import Post


class UserSerializer(serializers.ModelSerializer):
    """Serializer for User model."""

    class Meta:
        model = User
        fields = ["id", "username", "email", "date_joined"]
        read_only_fields = ["id", "date_joined"]


class PostSerializer(serializers.ModelSerializer):
    """Serializer for Post model."""

    author = UserSerializer(read_only=True)

    class Meta:
        model = Post
        fields = ["id", "title", "content", "author", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]





