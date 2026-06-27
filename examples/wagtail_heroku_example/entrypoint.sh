#!/usr/bin/env bash
set -euo pipefail

python manage.py migrate --noinput
python manage.py check --deploy

echo "Starting gunicorn..."
exec gunicorn wagtail_heroku_example.wsgi:application \
  --bind 0.0.0.0:${PORT:-8000} \
  --workers ${WEB_CONCURRENCY:-3} \
  --access-logfile '-' \
  --error-logfile '-'
