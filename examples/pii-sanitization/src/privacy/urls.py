from rest_framework.routers import DefaultRouter

from .views import PrivacyViewSet

router = DefaultRouter()
router.register("privacy", PrivacyViewSet, basename="privacy")

urlpatterns = router.urls
