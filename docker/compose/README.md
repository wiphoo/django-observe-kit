# Docker Compose Integration Stack

This directory contains the Docker Compose configuration for the integration test stack, which provides a complete observability infrastructure for local development and testing.

## Directory Structure

```
docker/compose/
├── integration.yml              # Main Docker Compose file
├── README.md                    # This file
├── .env.example                 # Environment variables template
├── configs/                     # Service configuration files
│   ├── otel-collector.yaml     # OTEL Collector configuration
│   └── prometheus.yml           # Prometheus configuration
└── scripts/                     # Initialization and utility scripts
    └── init_hyperdx.py          # Python script for HyperDX setup (MongoDB + user creation)
```

## Services

### Core Services

- **otel-collector**: OpenTelemetry Collector that receives, processes, and exports telemetry data
  - OTLP HTTP: `http://localhost:4318`
  - OTLP gRPC: `http://localhost:4317`
  - Metrics: `http://localhost:8888`
  - Health: `http://localhost:13133`

- **prometheus**: Metrics collection and storage
  - Web UI: `http://localhost:9090`

- **clickhouse**: Time-series database for observability data storage
  - HTTP: `http://localhost:28123` (shifted from 8123 to avoid conflicts)
  - Native: `localhost:29000` (shifted from 9000 to avoid conflicts)

- **mongodb**: Session storage for HyperDX
  - Port: `localhost:27017`

- **hyperdx**: Unified observability platform (logs, traces, metrics visualization)
  - Web UI: `http://localhost:8080`

### Utility Services

- **init-hyperdx**: One-time initialization script that creates default admin user and team
- **healthcheck**: Displays service URLs and status when all services are ready
- **clickhouse-client**: Interactive ClickHouse client (dev profile only)

## Quick Start

### 1. Start the Stack

```bash
# From project root
make integration-up
```

This will:
- Start all services
- Wait for services to be healthy
- Initialize HyperDX with default admin user
- Display service URLs and login credentials

### 2. Access Services

After startup, you'll see output like:

```
✅ All services are healthy!

Service URLs:
  - OTEL Collector HTTP: http://localhost:4318
  - OTEL Collector gRPC: http://localhost:4317
  - Prometheus: http://localhost:9090
  - HyperDX: http://localhost:8080
  - ClickHouse HTTP: http://localhost:28123
  - ClickHouse Native: localhost:29000
  - MongoDB: localhost:27017

HyperDX Login:
  Email: admin@example.com
  Password: Admin123!@#$
```

### 3. Stop the Stack

```bash
# Stop services (preserves data)
make integration-stop

# Stop and remove all data
make integration-clean
```

## Port Strategy

This stack uses a **shifted port strategy** (inspired by Forma's approach) to minimize conflicts with other services:

- **ClickHouse ports are shifted**: `28123` (HTTP) and `29000` (Native) instead of standard `8123` and `9000`
- **OTEL Collector ports remain standard**: `4317` (gRPC) and `4318` (HTTP) - less likely to conflict
- **Other services use standard ports**: `8080` (HyperDX), `9090` (Prometheus), `27017` (MongoDB)

All ports can be customized via environment variables. See `.env.example` for details.

### Why Shifted Ports?

- **Avoid conflicts**: Standard ClickHouse ports (8123, 9000) are commonly used by other services
- **Clear separation**: Makes it obvious these are integration test services
- **Flexibility**: Easy to run multiple stacks side-by-side with different port mappings

## Configuration

### Environment Variables

Copy `.env.example` to `.env` and customize as needed:

```bash
cp docker/compose/.env.example docker/compose/.env
```

Key variables:
- Port mappings (if you need to avoid conflicts)
- `TELEMETRY_ENVIRONMENT`: Environment tag for telemetry
- `HYPERDX_ADMIN_*`: Default admin credentials

### Service Configuration

- **OTEL Collector**: `configs/otel-collector.yaml`
  - Configures receivers, processors, and exporters
  - Exports to ClickHouse for HyperDX

- **Prometheus**: `configs/prometheus.yml`
  - Scrapes OTEL Collector metrics
  - Scrapes Django app metrics (if running)

## HyperDX Setup

HyperDX is automatically initialized with:
- **Default Team**: "Default Team" (slug: "default")
- **Admin User**: `admin@example.com` / `Admin123!@#$` (configurable via environment variables)

⚠️ **Security Warning**: These are default credentials for local development only! Change them in production.

The initialization is handled by the `init-hyperdx` service, which:
1. Waits for MongoDB to be healthy and creates required collections
2. Waits for HyperDX API to be ready
3. Creates admin user via HyperDX API (with proper password hashing)
4. Verifies ClickHouse datasource is configured (auto-created via environment variables)

The ClickHouse datasource is automatically configured via `DEFAULT_CONNECTIONS` and `DEFAULT_SOURCES` environment variables in the HyperDX service. This ensures that logs, traces, and metrics from ClickHouse are immediately available in HyperDX without manual configuration.

## Development Tools

### ClickHouse Client

Access ClickHouse interactively:

```bash
docker compose -f docker/compose/integration.yml --profile dev run --rm clickhouse-client
```

### View Logs

```bash
# All services
make integration-logs

# Specific service
docker compose -f docker/compose/integration.yml logs -f hyperdx
```

### Check Status

```bash
make integration-status
```

## Troubleshooting

### Port Conflicts

If you get port conflicts, check which ports are in use:

```bash
make integration-check-ports
```

Then update `.env` with different port numbers.

### Services Not Starting

1. Check service logs:
   ```bash
   docker compose -f docker/compose/integration.yml logs <service-name>
   ```

2. Verify health checks:
   ```bash
   docker compose -f docker/compose/integration.yml ps
   ```

3. Check resource limits:
   - Services have resource limits to prevent resource exhaustion
   - Adjust in `integration.yml` if needed

### HyperDX Login Issues

If you can't log in to HyperDX:

1. Check if init-hyperdx service completed:
   ```bash
   docker compose -f docker/compose/integration.yml logs init-hyperdx
   ```

2. Re-run initialization:
   ```bash
   docker compose -f docker/compose/integration.yml run --rm init-hyperdx
   ```

3. Verify MongoDB has the user:
   ```bash
   docker compose -f docker/compose/integration.yml exec mongodb mongosh hyperdx --eval "db.users.find().pretty()"
   ```

### Data Persistence

All data is stored in Docker volumes with explicit names:
- `observe_kit-prometheus-data`: Prometheus metrics
- `observe_kit-clickhouse-data`: Observability data
- `observe_kit-mongodb-data`: HyperDX sessions

To reset everything:
```bash
make integration-clean
```

## Architecture

```
┌─────────────┐
│ Django App  │
└──────┬──────┘
       │
       ├─── Metrics ────► Prometheus (scrape /metrics)
       │
       └─── Traces/Logs ──► OTEL Collector (4318/4317) ──► ClickHouse (ch-server) ──► HyperDX
                                    │
                                    └──► Prometheus (collector metrics)
```

### Network Architecture

- All services run on the `observe_kit-network` Docker network
- ClickHouse has network alias `ch-server` for consistent service discovery
- Services communicate via Docker network (internal ports)
- Only necessary ports are exposed to the host (shifted to avoid conflicts)

## Makefile Targets

- `make integration-up`: Start the stack
- `make integration-down`: Stop and remove everything (alias for integration-clean)
- `make integration-stop`: Stop services (preserve data)
- `make integration-clean`: Stop and remove all data
- `make integration-status`: Show service status and URLs
- `make integration-logs`: View logs from all services
- `make integration-check-ports`: Check for port conflicts
- `make integration-health`: Check health of all services
- `make integration-hyperdx-login`: Display HyperDX login credentials
- `make integration-hyperdx-open`: Open HyperDX UI in browser
- `make integration-prometheus-open`: Open Prometheus UI in browser

## Additional Resources

- [OpenTelemetry Documentation](https://opentelemetry.io/docs/)
- [Prometheus Documentation](https://prometheus.io/docs/)
- [HyperDX Documentation](https://www.hyperdx.io/docs)
- [ClickHouse Documentation](https://clickhouse.com/docs)


