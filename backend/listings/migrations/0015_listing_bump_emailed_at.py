from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0014_add_listing_view_count"),
    ]

    operations = [
        migrations.AddField(
            model_name="listing",
            name="bump_emailed_at",
            field=models.DateTimeField(
                null=True,
                blank=True,
                help_text="Last time a 'still available?' re-engagement email was sent to the owner.",
            ),
        ),
    ]
