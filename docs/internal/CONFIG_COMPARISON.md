# Configuration Comparison: Our Setup vs Reference (Toffoli/Forma/infra)

This document compares our observability stack configuration with the reference implementation from `/home/terng/work/Toffoli/Forma/infra`.

## Summary of Changes

All configurations have been updated to align with the reference implementation, incorporating best practices and missing features.

---

## 1. OTEL Collector Configuration

### Key Improvements Made:

#### ✅ Added Extensions
- **health_check** (port 13133): Health monitoring endpoint
- **pprof** (port 1777): Go profiling for performance analysis
- **zpages** (port 55679): Live debugging interface

#### ✅ Enhanced Receivers
- Added **CORS configuration** for HTTP receiver to allow localhost origins
- Better documentation of endpoints

#### ✅ Improved Processors
- **Resource processor**: Adds `deployment.environment`, `service.version`, and `collector.name` attributes
- **Memory limiter**: Added `spike_limit_mib` for better backpressure handling
- **Batch processor**: Increased timeout to 10s and added `send_batch_max_size` (2048)

#### ✅ Enhanced ClickHouse Exporter
- **Environment variable support**: Uses `${env:CLICKHOUSE_ENDPOINT}` for flexibility
- **TTL configuration**: Added 30-day retention (720h)
- **Retry configuration**: Comprehensive retry settings with exponential backoff
- **Sending queue**: Added queue configuration for better throughput
- **Timeout**: Increased to 10s for better reliability

#### ✅ Improved Prometheus Exporter
- Added `namespace` and `const_labels` for better metric organization

#### ✅ Better Logging
- Replaced `debug` exporter with `logging` exporter (standard)
- Added sampling configuration

#### ✅ Enhanced Service Configuration
- Added all extensions to service
- Better telemetry configuration with initial fields

### Comparison Table

| Feature | Before | After (Aligned with Reference) |
|---------|--------|--------------------------------|
| Extensions | None | health_check, pprof, zpages |
| CORS | No | Yes (localhost origins) |
| Resource Processor | No | Yes (env, version, collector name) |
| Memory Limiter Spike | No | Yes (128 MiB) |
| Batch Timeout | 1s | 10s |
| Batch Max Size | 1024 | 2048 |
| ClickHouse TTL | No | 720h (30 days) |
| ClickHouse Retry | Basic | Full retry config |
| ClickHouse Queue | No | Yes (4 consumers, 1000 queue) |
| Environment Variables | Hardcoded | Full env var support |
| Prometheus Namespace | No | Yes ("otel") |
| Logging Exporter | debug | logging (standard) |

---

## 2. Prometheus Configuration

### Key Improvements Made:

#### ✅ Enhanced Global Configuration
- Added **external_labels** with `environment` and `cluster` labels
- Better scrape intervals (10s global, 5s for app)

#### ✅ Improved Scrape Configs
- **OTEL Collector**: Changed from port 8889 to 8888 (collector's own metrics)
- **Django App**: Added service labels and better documentation
- Added alternative target options for different deployment scenarios

### Comparison Table

| Feature | Before | After (Aligned with Reference) |
|---------|--------|--------------------------------|
| External Labels | No | Yes (environment, cluster) |
| Scrape Interval | 15s | 10s (global), 5s (app) |
| OTEL Collector Port | 8889 | 8888 (correct endpoint) |
| Service Labels | No | Yes |
| Documentation | Basic | Comprehensive with alternatives |

---

## 3. Docker Compose Configuration

### Key Improvements Made:

#### ✅ OTEL Collector Service
- **Environment Variables**: Added `CLICKHOUSE_ENDPOINT`, `CLICKHOUSE_DATABASE`, `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD`
- **Telemetry Environment**: Added `TELEMETRY_ENVIRONMENT` and `SERVICE_VERSION`
- **Port Mappings**: Added health check (13133), pprof (1777), and zpages (55679) ports
- **Health Check**: Changed to use health check endpoint (13133) instead of OTLP port

### Comparison Table

| Feature | Before | After (Aligned with Reference) |
|---------|--------|--------------------------------|
| ClickHouse Env Vars | No | Yes (endpoint, database, user, password) |
| Telemetry Env Vars | No | Yes (environment, version) |
| Health Check Port | No | Yes (13133) |
| pprof Port | No | Yes (1777) |
| zpages Port | No | Yes (55679) |
| Health Check Endpoint | OTLP port | Health check endpoint |

---

## 4. Architecture Alignment

### Data Flow (Now Matches Reference)

```
Application
    ↓
OTEL Collector (receives OTLP)
    ↓
    ├─→ ClickHouse (traces, logs, metrics) → HyperDX UI
    ├─→ Prometheus (metrics only)
    └─→ Logging (debug output)
```

### Key Architectural Decisions (Aligned with Reference)

1. **Prometheus-First for Metrics**: Direct scraping of `/metrics` endpoint
2. **OTEL Collector for Traces/Logs**: Centralized collection and processing
3. **ClickHouse as Storage**: High-performance columnar storage for HyperDX
4. **Resource Attribution**: Automatic addition of environment and version info
5. **Retention Policy**: 30-day TTL for observability data

---

## 5. Configuration Files Updated

1. ✅ `docker/compose/otel-collector-config.yaml` - Complete rewrite aligned with reference
2. ✅ `docker/compose/prometheus-config.yml` - Enhanced with labels and better config
3. ✅ `docker/compose/integration.yml` - Added environment variables and ports

---

## 6. Next Steps / Recommendations

### Optional Enhancements (from Reference)

1. **ClickHouse Schema Initialization**: The reference has custom schema files (`clickhouse-schema.sql`, `init-hyperdx-schema.sql`) that create optimized tables with proper indexing. Consider adding these if you need custom table structures.

2. **Grafana Integration**: The reference includes Grafana configuration. If you want Grafana dashboards, you can add them.

3. **Custom Resource Attributes**: You can add more custom attributes in the resource processor if needed.

4. **Sampling Configuration**: For high-traffic scenarios, consider adding sampling in the application layer.

---

## 7. Testing the Configuration

After these changes, verify:

1. **OTEL Collector Health**:
   ```bash
   curl http://localhost:13133
   ```

2. **OTEL Collector Metrics**:
   ```bash
   curl http://localhost:8888/metrics
   ```

3. **Prometheus Scraping**:
   - Check Prometheus UI at http://localhost:9090
   - Verify `otel-collector` and `django-app` targets are up

4. **ClickHouse Connection**:
   - Verify OTEL Collector can connect to ClickHouse
   - Check logs: `docker compose -f docker/compose/integration.yml logs otel-collector`

5. **HyperDX Data Flow**:
   - Send test traces/logs from your app
   - Verify data appears in HyperDX UI

---

## 8. Environment Variables Reference

### OTEL Collector
- `CLICKHOUSE_ENDPOINT` (default: `clickhouse:9000`)
- `CLICKHOUSE_DATABASE` (default: `hyperdx`)
- `CLICKHOUSE_USER` (default: `default`)
- `CLICKHOUSE_PASSWORD` (default: empty)
- `TELEMETRY_ENVIRONMENT` (default: `integration`)
- `SERVICE_VERSION` (default: `unknown`)

### Override Example
```bash
export CLICKHOUSE_ENDPOINT=clickhouse:9000
export TELEMETRY_ENVIRONMENT=production
export SERVICE_VERSION=1.2.3
docker compose -f docker/compose/integration.yml up -d
```

---

## Conclusion

All configurations have been successfully aligned with the reference implementation from `/home/terng/work/Toffoli/Forma/infra`. The setup now includes:

- ✅ Comprehensive OTEL Collector configuration with extensions
- ✅ Enhanced Prometheus configuration with labels
- ✅ Environment variable support for flexibility
- ✅ Better error handling and retry logic
- ✅ Health checks and debugging endpoints
- ✅ Proper resource attribution
- ✅ Optimized batch processing

The configuration is production-ready and follows OpenTelemetry best practices.


