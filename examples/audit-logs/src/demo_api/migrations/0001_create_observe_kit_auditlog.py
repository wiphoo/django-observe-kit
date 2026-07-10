from __future__ import annotations

from django.db import migrations


def create_auditlog_table(apps, schema_editor):
    from observe_kit.audit.models import AuditLog

    existing_tables = schema_editor.connection.introspection.table_names()
    if AuditLog._meta.db_table in existing_tables:
        return

    schema_editor.create_model(AuditLog)


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("observe_kit.audit", "0001_initial"),
    ]

    operations = [
        # Forward is a defensive no-op in normal use: the observe_kit.audit
        # dependency already creates the shared AuditLog table. Reverse must
        # NOT drop it — that table is owned by observe_kit.audit.0001_initial,
        # which can still be applied after this demo migration is unwound.
        migrations.RunPython(create_auditlog_table, migrations.RunPython.noop),
    ]
