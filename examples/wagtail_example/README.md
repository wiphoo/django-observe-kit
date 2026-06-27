# Wagtail Example

This example demonstrates Wagtail CMS integration with observe_kit.

## Setup

1. Install dependencies:
```bash
pip install -e ../.. Django wagtail
```

2. Configure database:
```bash
python manage.py migrate
```

3. Create a superuser:
```bash
python manage.py createsuperuser
```

4. Run the server:
```bash
python manage.py runserver
```

## Features Demonstrated

- Wagtail hooks integration (publish/unpublish/delete)
- Admin request tagging (`framework="wagtail_admin"`)
- Page observability
- Metrics for Wagtail events

## Endpoints

- `http://localhost:8000/admin/` - Wagtail admin (tagged as wagtail_admin)
- `http://localhost:8000/` - Public site
- `http://localhost:8000/healthz` - Health check
- `http://localhost:8000/metrics` - Prometheus metrics (includes Wagtail metrics)

## Key Features

1. **Admin Request Tagging**: Requests to `/admin/` are automatically tagged with `framework="wagtail_admin"`
2. **Wagtail Hooks**: Page publish/unpublish/delete events are automatically tracked
3. **Metrics**: Wagtail-specific metrics:
   - `wagtail_pages_published_total{tenant}`
   - `wagtail_pages_unpublished_total{tenant}`
   - `wagtail_pages_deleted_total{tenant}`
4. **Audit Logging**: All page operations are logged to AuditLog

## Testing the Integration

1. **Access Admin**:
   - Visit http://localhost:8000/admin/
   - Login with superuser credentials
   - Check logs - should see `framework="wagtail_admin"` in spans

2. **Publish a Page**:
   - Create/edit a page in admin
   - Click "Publish"
   - Check logs for `wagtail_publish` event
   - Check metrics for `wagtail_pages_published_total`

3. **Unpublish a Page**:
   - Unpublish a page
   - Check logs for `wagtail_unpublish` event

4. **Delete a Page**:
   - Delete a page
   - Check logs for `wagtail_delete` event

## Observability in Action

All Wagtail operations will show:
- Structured JSON logs with event type
- Trace IDs for correlation
- Tenant IDs (if configured)
- Metrics in Prometheus format
- Audit log entries





