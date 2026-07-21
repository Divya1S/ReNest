from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0015_listing_bump_emailed_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="listing",
            name="repost_token",
            field=models.CharField(
                max_length=64,
                null=True,
                blank=True,
                db_index=True,
                help_text="Short-lived signed token for one-click relist from an expiry notification.",
            ),
        ),
        migrations.AddField(
            model_name="listing",
            name="repost_token_expires_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
    ]
