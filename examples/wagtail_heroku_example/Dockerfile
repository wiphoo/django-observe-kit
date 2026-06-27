FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=wagtail_heroku_example.settings

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e . \
    && pip install --no-cache-dir -r examples/wagtail_heroku_example/requirements.txt

WORKDIR /app/examples/wagtail_heroku_example

RUN python manage.py collectstatic --noinput

ENTRYPOINT ["./entrypoint.sh"]
