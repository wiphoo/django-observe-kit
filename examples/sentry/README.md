# Sentry Example

Goal: send an intentional Django error to Sentry with request context attached.

## What This Example Includes

- `GET /api/demo/failure/` to trigger one captured exception
- `observe_kit` request and Sentry context middleware
- A minimal setup focused on error capture, not traces or metrics

## Quick Start

```bash
cd examples/sentry
uv sync
uv run python src/manage.py migrate
cp docker/compose/.env.example docker/compose/.env
docker compose -f docker/compose/integration.yml --env-file docker/compose/.env up -d
OBSERVE_KIT_SENTRY_DSN=<paste-local-glitchtip-dsn> \
OBSERVE_KIT_SENTRY_ENVIRONMENT=development \
uv run python src/manage.py runserver
```

## Trigger The Demo

```bash
curl http://127.0.0.1:8000/api/demo/failure/
```

Then open the local GlitchTip instance:

- GlitchTip: `http://127.0.0.1:8080`
- Redis insight is not required for this demo

Register the first user in GlitchTip, then create one organization and one project in the UI. Copy the project DSN into `OBSERVE_KIT_SENTRY_DSN` before starting the Django app.

## What To Verify

- The endpoint returns a `500`
- A new GlitchTip event appears with the message `Intentional Sentry demo failure`
- Request metadata is attached to the event

## Relevant Env Vars

- `OBSERVE_KIT_SENTRY_DSN`
- `OBSERVE_KIT_SENTRY_ENVIRONMENT`
- `OBSERVE_KIT_SERVICE_NAME`
- `GLITCHTIP_DOMAIN`

## Tests

```bash
uv run pytest
```
