from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0017_listing_moderation_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="listing",
            name="building",
            field=models.CharField(
                max_length=120,
                blank=True,
                help_text="Dorm or building name for proximity filtering.",
            ),
        ),
        migrations.AddIndex(
            model_name="listing",
            index=models.Index(fields=["building", "status"], name="listing_building_status_idx"),
        ),
    ]
