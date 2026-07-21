from __future__ import annotations

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0013_add_fulltext_search_gin_index"),
    ]

    operations = [
        migrations.CreateModel(
            name="ListingViewEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "listing",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="view_events",
                        to="listings.listing",
                    ),
                ),
                ("session_key", models.CharField(max_length=64, db_index=True)),
                ("viewed_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "unique_together": {("listing", "session_key")},
            },
        ),
    ]
