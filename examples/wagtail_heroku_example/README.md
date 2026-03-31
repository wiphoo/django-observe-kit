# Wagtail Heroku Container Example

End-to-end example that deploys a Wagtail site with `observe_kit` to Heroku's container stack. It demonstrates how to:

- Run the app inside a Docker image with `gunicorn`
- Use Neon as the managed Postgres database
- Send traces/logs/metrics to HyperDX (OTLP HTTP) and Sentry
- Expose Prometheus metrics that can be scraped by Grafana Agent or Prometheus server
- Keep everything configurable through Heroku config vars

## Prerequisites

- Docker + Docker Compose (for local builds)
- Heroku CLI (`heroku`)
- Neon database (copy the `postgres://` connection string)
- Sentry project DSN (optional but recommended)
- HyperDX account + API key
- Grafana Agent or Prometheus server that can scrape HTTPS endpoints

Install dependencies from the repo root:

```bash
pip install -e .[dev]
cd examples/wagtail_heroku_example
pip install -r requirements.txt
```

## Project Layout

```
wagtail_heroku_example/
├── cms_app/             # Minimal Wagtail app
│   ├── migrations/
│   ├── apps.py
│   └── models.py
├── Dockerfile           # Container build
├── entrypoint.sh        # Runs migrations + server
├── heroku.yml           # Heroku container stack config
├── manage.py
├── requirements.txt
├── settings.py
├── urls.py
└── README.md
```

## Local Run

```bash
export DJANGO_SECRET_KEY="dev-secret"
export DJANGO_ALLOWED_HOSTS="localhost,127.0.0.1"
export DATABASE_URL="sqlite:///db.sqlite3"
export DEBUG=1

python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

Visit `http://localhost:8000/` and hit `/metrics` to confirm Prometheus output.

## Docker Build

Build from the repository root so the Docker context includes the `observe_kit` source:

```bash
docker build -t wagtail-heroku-example -f examples/wagtail_heroku_example/Dockerfile .
docker run --rm -p 8000:8000 \
  -e DJANGO_SECRET_KEY="dev" \
  -e DJANGO_ALLOWED_HOSTS="localhost" \
  -e DATABASE_URL="sqlite:///db.sqlite3" \
  wagtail-heroku-example
```

## Deploy to Heroku (Container Registry)

1. Create the Heroku app and switch to the container stack:
   ```bash
   heroku apps:create my-wagtail-observe-kit
   heroku stack:set container -a my-wagtail-observe-kit
   ```
2. Set config vars (adjust values to your environment):
   ```bash
   heroku config:set -a my-wagtail-observe-kit \
     DJANGO_SECRET_KEY="prod-secret" \
     DJANGO_ALLOWED_HOSTS="my-wagtail-observe-kit.herokuapp.com" \
     DJANGO_DEBUG="0" \
     SENTRY_DSN="https://key.ingest.sentry.io/123" \
     SENTRY_ENVIRONMENT="production" \
     DATABASE_URL="postgres://<neon-user>:<pwd>@<host>/<db>?sslmode=require" \
     OTEL_EXPORTER_OTLP_ENDPOINT="https://ingest.hyperdx.io/v1/traces" \
     OTEL_EXPORTER_OTLP_HEADERS="authorization=<hyperdx-api-key>" \
     OTEL_SERVICE_NAME="wagtail-heroku" \
     PROMETHEUS_METRICS_BASIC_AUTH="username:password"
   ```
   - **Neon**: grab the connection string from the Neon dashboard and append `sslmode=require`.
   - **Sentry**: DSN + environment name ensures tagged releases. Heroku automatically injects `HEROKU_RELEASE_VERSION` which we forward to Sentry.
   - **HyperDX**: use their OTLP HTTPS endpoint and pass the API key inside `OTEL_EXPORTER_OTLP_HEADERS`. HyperDX also supports log drains—enable one in the HyperDX dashboard to ingest Heroku logs (our logs are JSON, so they work out-of-the-box).
   - **Prometheus auth (optional)**: if you need basic auth on `/metrics`, set `PROMETHEUS_METRICS_BASIC_AUTH` to `user:pass`. Configure your scraper accordingly.
3. Push and deploy the container:
   ```bash
   heroku container:login
   heroku container:push web -a my-wagtail-observe-kit
   heroku container:release web -a my-wagtail-observe-kit
   ```
4. Run migrations (also handled on deploy via `heroku.yml` release phase but you can trigger manually):
   ```bash
   heroku run python manage.py migrate -a my-wagtail-observe-kit
   ```

## Observability Integrations

### Sentry
- Set `SENTRY_DSN` and (optionally) `SENTRY_ENVIRONMENT`, `SENTRY_TRACES_SAMPLE_RATE`.
- `observe_kit.sentry.init_sentry` wires automatic context, user data, and PII scrubbing.

### HyperDX (OTLP traces + logs)
- `init_tracing` points to `OTEL_EXPORTER_OTLP_ENDPOINT`.
- Provide the HyperDX API key through `OTEL_EXPORTER_OTLP_HEADERS="authorization=<api-key>"`.
- Logs are structured JSON on stdout—connect Heroku log drain to HyperDX to ingest them.

### Neon Postgres
- Supply Neon’s `DATABASE_URL`; `dj-database-url` parses it and sets SSL mode.
- Connection pooling is handled via `CONN_MAX_AGE` (default 600 seconds; override with `DB_CONN_MAX_AGE`).

### Grafana / Prometheus
- `/metrics` exposes Prometheus metrics (HTTP request latency, DB timings, Wagtail hooks).
- Sample scrape config (Grafana Agent or Prometheus):
  ```yaml
  scrape_configs:
    - job_name: wagtail-heroku
      scrape_interval: 15s
      metrics_path: /metrics
      scheme: https
      basic_auth:
        username: <user>
        password: <pass>
      static_configs:
        - targets: ["my-wagtail-observe-kit.herokuapp.com"]
  ```
- Grafana Cloud can connect via the Agent; ensure the Agent can reach Heroku over HTTPS.

## Environment Variables Summary

| Variable | Purpose |
| --- | --- |
| `DJANGO_SECRET_KEY` | Required secret key |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hosts for Django |
| `DJANGO_DEBUG` | `1`/`0` to toggle debug |
| `DATABASE_URL` | Neon/Postgres URL with `sslmode=require` |
| `DB_CONN_MAX_AGE` | Override default connection pool timeout |
| `SENTRY_DSN` | Enables Sentry integration |
| `SENTRY_ENVIRONMENT` | Tag Sentry events with environment name |
| `SENTRY_TRACES_SAMPLE_RATE` | Override default traces sample rate |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | HyperDX OTLP HTTP endpoint |
| `OTEL_EXPORTER_OTLP_HEADERS` | Headers (e.g., `authorization=<api-key>`) for HyperDX |
| `OTEL_SERVICE_NAME` | Override OTEL service name (default `wagtail-heroku`) |
| `PROMETHEUS_METRICS_BASIC_AUTH` | Optional `user:pass` to protect `/metrics` |
| `DJANGO_SECURE_PROXY_SSL_HEADER` | Set to `HTTP_X_FORWARDED_PROTO,https` for custom proxies (default already set) |

## Notes

- `entrypoint.sh` runs migrations automatically on container start; Heroku release phase also runs migrations.
- Static files are served via WhiteNoise; collectstatic runs during Docker build.
- Health + metrics endpoints come from `observe_kit.urls` and require no extra code.

Happy observing!
