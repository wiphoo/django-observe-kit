"""App configuration for the Heroku Wagtail example."""

from django.apps import AppConfig


class CmsAppConfig(AppConfig):
    """Configure CMS app for Heroku example."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "cms_app"
