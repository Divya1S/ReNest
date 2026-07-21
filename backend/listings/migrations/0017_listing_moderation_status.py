from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0016_listing_repost_token"),
    ]

    operations = [
        migrations.AddField(
            model_name="listing",
            name="moderation_status",
            field=models.CharField(
                max_length=16,
                choices=[
                    ("pending", "Pending"),
                    ("approved", "Approved"),
                    ("flagged", "Flagged"),
                ],
                default="pending",
                db_index=True,
            ),
        ),
        migrations.AddField(
            model_name="listing",
            name="moderation_flag_reason",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
    ]
