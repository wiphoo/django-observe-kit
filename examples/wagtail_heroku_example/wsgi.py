"""WSGI config for Wagtail Heroku example."""

import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wagtail_heroku_example.settings")

application = get_wsgi_application()
