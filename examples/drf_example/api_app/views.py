"""DRF ViewSets demonstrating observe_kit integration."""

import logging

from django.contrib.auth.models import User
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from observe_kit.audit import audit
from observe_kit.context import get_request_context

from .models import Post
from .serializers import PostSerializer, UserSerializer

logger = logging.getLogger(__name__)


class UserViewSet(viewsets.ModelViewSet):
    """
    ViewSet for User model.
    
    This ViewSet will automatically be detected by DRFIntegrationMiddleware
    and spans will be named as:
    - drf.UserViewSet.list (GET /api/users/)
    - drf.UserViewSet.create (POST /api/users/)
    - drf.UserViewSet.retrieve (GET /api/users/{id}/)
    - drf.UserViewSet.update (PUT /api/users/{id}/)
    - drf.UserViewSet.destroy (DELETE /api/users/{id}/)
    """

    queryset = User.objects.all()
    serializer_class = UserSerializer

    def list(self, request, *args, **kwargs):
        """List users - automatically detected as 'list' action."""
        context = get_request_context()
        logger.info("user_list_api", extra={"route": context.route, "trace_id": context.trace_id})
        
        response = super().list(request, *args, **kwargs)
        return response

    def create(self, request, *args, **kwargs):
        """Create user - automatically detected as 'create' action."""
        context = get_request_context()
        logger.info("user_create_api", extra={"route": context.route})
        
        # Audit log
        audit(
            actor=request.user if request.user.is_authenticated else None,
            action="create_user",
            obj=None,
            request=request,
        )
        
        return super().create(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        """
        Custom action - will be detected as 'drf.UserViewSet.activate'.
        
        Example: POST /api/users/{id}/activate/
        """
        user = self.get_object()
        # Custom logic here
        
        context = get_request_context()
        logger.info("user_activate", extra={"route": context.route, "user_id": user.id})
        
        audit(
            actor=request.user if request.user.is_authenticated else None,
            action="activate_user",
            obj=user,
            request=request,
        )
        
        return Response({"status": "activated", "user_id": user.id})


class PostViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Post model.
    
    Spans will be named as:
    - drf.PostViewSet.list
    - drf.PostViewSet.create
    - drf.PostViewSet.retrieve
    - etc.
    """

    queryset = Post.objects.all()
    serializer_class = PostSerializer

    def perform_create(self, serializer):
        """Override to set author automatically."""
        serializer.save(author=self.request.user if self.request.user.is_authenticated else None)
        
        # Audit log
        audit(
            actor=self.request.user if self.request.user.is_authenticated else None,
            action="create_post",
            obj=serializer.instance,
            request=self.request,
        )

    def perform_destroy(self, instance):
        """Override to add audit logging."""
        audit(
            actor=self.request.user if self.request.user.is_authenticated else None,
            action="delete_post",
            obj=instance,
            request=self.request,
        )
        super().perform_destroy(instance)





