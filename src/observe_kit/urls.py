from django.urls import path

from .metrics import metrics_view

urlpatterns = [path("metrics", metrics_view.as_view())]
