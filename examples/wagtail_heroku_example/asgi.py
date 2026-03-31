"""ASGI config for Wagtail Heroku example."""

import os
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wagtail_heroku_example.settings")

application = get_asgi_application()
