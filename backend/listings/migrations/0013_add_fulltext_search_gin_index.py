from __future__ import annotations

from django.db import migrations


def add_gin_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS listings_listing_fts_gin
            ON listings_listing
            USING gin(
                to_tsvector(
                    'english',
                    coalesce(title, '') || ' ' ||
                    coalesce(description, '') || ' ' ||
                    coalesce(pickup_zone, '')
                )
            )
            """
        )


def remove_gin_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS listings_listing_fts_gin")


class Migration(migrations.Migration):
    atomic = False  # required for CONCURRENTLY

    dependencies = [
        ("listings", "0012_add_composite_indexes"),
    ]

    operations = [
        migrations.RunPython(add_gin_index, remove_gin_index),
    ]
