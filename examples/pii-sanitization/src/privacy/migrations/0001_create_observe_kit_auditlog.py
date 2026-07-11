from __future__ import annotations

from django.db import migrations


def create_auditlog_table(apps, schema_editor):
    from observe_kit.audit.models import AuditLog

    if AuditLog._meta.db_table in schema_editor.connection.introspection.table_names():
        return

    schema_editor.create_model(AuditLog)


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        # The audit app's label is "audit" (last component of
        # "observe_kit.audit"), which is what the migration graph is keyed on.
        ("audit", "0001_initial"),
    ]

    operations = [
        # Forward is a defensive no-op in normal use: the audit app dependency
        # already creates the shared AuditLog table. Reverse must NOT drop it —
        # that table is owned by audit.0001_initial, which can still be applied
        # after this demo migration is unwound.
        migrations.RunPython(create_auditlog_table, migrations.RunPython.noop),
    ]
