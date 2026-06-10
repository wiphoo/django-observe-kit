from rest_framework.routers import DefaultRouter

from .views import AuditLogViewSet, QuoteViewSet

router = DefaultRouter()
router.register("quotes", QuoteViewSet, basename="quote")
router.register("audit", AuditLogViewSet, basename="audit")

urlpatterns = router.urls
