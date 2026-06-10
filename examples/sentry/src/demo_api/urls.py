from rest_framework.routers import DefaultRouter

from .views import DemoViewSet

router = DefaultRouter()
router.register("demo", DemoViewSet, basename="demo")

urlpatterns = router.urls
