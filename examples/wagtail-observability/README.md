# Wagtail Observability Example

Goal: verify `observe_kit` across a real Wagtail CMS workflow, including editor activity in `/admin/`, public page delivery, Prometheus metrics, and OTEL traces/logs.

## What This Example Includes

- A minimal Wagtail site with a home page and one article page
- `bootstrap_wagtail_demo` to create the admin user, Site config, and starter content
- `/admin/` for Wagtail editor activity
- `/metrics` for Prometheus-compatible metrics
- A local HyperDX + Prometheus + Grafana Docker stack

## Quick Start

```bash
cd examples/wagtail-observability
uv sync
cp docker/compose/.env.example docker/compose/.env
docker compose -f docker/compose/integration.yml --env-file docker/compose/.env up -d
uv run python src/manage.py migrate
OBSERVE_KIT_OTEL_ENDPOINT=http://127.0.0.1:4318 \
OBSERVE_KIT_SERVICE_NAME=example-wagtail-observability \
OBSERVE_KIT_OTEL_SAMPLE_RATE=1.0 \
OBSERVE_KIT_ENABLE_CONSOLE_SPANS=0 \
uv run python src/manage.py bootstrap_wagtail_demo
OBSERVE_KIT_OTEL_ENDPOINT=http://127.0.0.1:4318 \
OBSERVE_KIT_SERVICE_NAME=example-wagtail-observability \
OBSERVE_KIT_OTEL_SAMPLE_RATE=1.0 \
OBSERVE_KIT_ENABLE_CONSOLE_SPANS=0 \
uv run python src/manage.py runserver 0.0.0.0:8000
```

> The server binds to `0.0.0.0:8000` so the Prometheus container can scrape it via
> `host.docker.internal:8000` (Django's default `127.0.0.1` isn't reachable through
> the Docker host-gateway). `host.docker.internal` is already in `ALLOWED_HOSTS`.

## Trigger The Demo

1. Open `http://127.0.0.1:8000/admin/` and sign in with the credentials from `docker/compose/.env`.
2. Edit the `Launch Announcement` page and publish a change.
3. Open the public page at `http://127.0.0.1:8000/launch-announcement/`.
4. Open `http://127.0.0.1:8000/metrics`.

## What To Verify

- Wagtail admin is reachable at `/admin/`
- The public page renders at `/launch-announcement/`
- `http_requests_total` and `http_request_duration_seconds` are present at `/metrics`
- Prometheus shows the `example-wagtail-observability` target as `UP`
- Grafana shows request data for both admin and public traffic
- HyperDX shows traces/logs for `/admin/` actions and public page requests

Login credentials from `docker/compose/.env`:

- Wagtail admin username: `editor`
- Wagtail admin password: `Admin123!@#$`
- HyperDX email: `admin@example.com`
- HyperDX password: `Admin123!@#$`
- Grafana username: `admin`
- Grafana password: `admin`

## Relevant Env Vars

- `OBSERVE_KIT_OTEL_ENDPOINT`
- `OBSERVE_KIT_SERVICE_NAME`
- `OBSERVE_KIT_OTEL_SAMPLE_RATE`
- `OBSERVE_KIT_ENABLE_CONSOLE_SPANS`
- `OBSERVE_KIT_WAGTAIL_ADMIN_USERNAME`
- `OBSERVE_KIT_WAGTAIL_ADMIN_EMAIL`
- `OBSERVE_KIT_WAGTAIL_ADMIN_PASSWORD`

## Tests

```bash
uv run pytest
```
