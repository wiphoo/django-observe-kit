# Local Wagtail Observability Stack

This stack combines HyperDX for traces/logs with Prometheus and Grafana for request metrics.

## Services

- `otel-collector`: receives OTLP traces and logs from the host-run Wagtail app
- `hyperdx`: local UI for traces and correlated logs
- `prometheus`: scrapes `/metrics` from the host-run Wagtail app
- `grafana`: preconfigured with a Prometheus datasource and starter dashboard

## Start

```bash
cp docker/compose/.env.example docker/compose/.env
docker compose -f docker/compose/integration.yml --env-file docker/compose/.env up -d
```

## Run The Wagtail App

```bash
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

> Binds to `0.0.0.0:8000` so the Prometheus container can scrape via
> `host.docker.internal:8000` (Django's default `127.0.0.1` isn't reachable
> through the Docker host-gateway). `host.docker.internal` is in `ALLOWED_HOSTS`.

## Verify

1. Sign in to Wagtail admin at `http://localhost:8000/admin/`.
2. Publish a change to the `Launch Announcement` page.
3. Open the public page at `http://localhost:8000/launch-announcement/`.
4. Check Prometheus targets at `http://localhost:9091/targets`.
5. Open the Grafana dashboard at `http://localhost:3001`.
6. Open HyperDX at `http://localhost:8080`.

## Stop

```bash
docker compose -f docker/compose/integration.yml --env-file docker/compose/.env down
```
