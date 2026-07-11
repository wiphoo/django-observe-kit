# Local Grafana Metrics Stack

This stack provisions Prometheus and Grafana for the metrics example.

## Services

- `prometheus`: scrapes the host-run Django app at `/metrics`
- `grafana`: preconfigured with the Prometheus datasource and a starter dashboard

## Start

```bash
cp docker/compose/.env.example docker/compose/.env
docker compose -f docker/compose/integration.yml --env-file docker/compose/.env up -d
```

## Run The Django App

```bash
uv run python src/manage.py migrate
# Bind to 0.0.0.0 so the Prometheus container can scrape via host.docker.internal:8000
# (Django's default 127.0.0.1 is not reachable through the Docker host-gateway).
uv run python src/manage.py runserver 0.0.0.0:8000
```

## Verify

1. Send traffic to the app.
2. Open Prometheus at `http://localhost:9090/targets` and confirm the Django target is `UP`.
3. Open Grafana at `http://localhost:3000` and load the `Observe Kit Metrics Overview` dashboard.

## Stop

```bash
docker compose -f docker/compose/integration.yml --env-file docker/compose/.env down
```
