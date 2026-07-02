from __future__ import annotations

from django.db import migrations


def create_auditlog_table(apps, schema_editor):
    from observe_kit.audit.models import AuditLog

    existing_tables = schema_editor.connection.introspection.table_names()
    if AuditLog._meta.db_table in existing_tables:
        return

    schema_editor.create_model(AuditLog)


def drop_auditlog_table(apps, schema_editor):
    from observe_kit.audit.models import AuditLog

    existing_tables = schema_editor.connection.introspection.table_names()
    if AuditLog._meta.db_table not in existing_tables:
        return

    schema_editor.delete_model(AuditLog)


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("observe_kit.audit", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_auditlog_table, drop_auditlog_table),
    ]
