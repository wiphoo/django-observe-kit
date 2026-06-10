# Local GlitchTip Stack

This stack provides a local Sentry-compatible backend for the Sentry example.

## Services

- `postgres`: GlitchTip database
- `redis`: task queue and cache
- `glitchtip-web`: the local GlitchTip UI and ingest API
- `glitchtip-worker`: background jobs

## Start

```bash
cp docker/compose/.env.example docker/compose/.env
docker compose -f docker/compose/integration.yml --env-file docker/compose/.env up -d
```

## First-Time Setup

1. Open `http://localhost:8080`.
2. Register the first user in GlitchTip.
3. Create an organization and a project.
4. Copy the project DSN into `OBSERVE_KIT_SENTRY_DSN` before starting the Django app.

## Run The Django App

```bash
OBSERVE_KIT_SENTRY_DSN=<paste-local-glitchtip-dsn> \
OBSERVE_KIT_SENTRY_ENVIRONMENT=development \
uv run python src/manage.py runserver
```

## Stop

```bash
docker compose -f docker/compose/integration.yml --env-file docker/compose/.env down
```
