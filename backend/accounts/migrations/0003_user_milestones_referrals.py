from __future__ import annotations

import secrets

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def assign_unique_referral_codes(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    existing = set()
    for user in User.objects.filter(referral_code=""):
        code = secrets.token_urlsafe(6)[:8]
        while code in existing:
            code = secrets.token_urlsafe(6)[:8]
        existing.add(code)
        user.referral_code = code
        user.save(update_fields=["referral_code"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_user_email_verified"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="milestone",
            field=models.CharField(
                blank=True,
                max_length=24,
                choices=[
                    ("", "None"),
                    ("first_rescue", "First Rescue"),
                    ("active_rescuer", "Active Rescuer"),
                    ("campus_hero", "Campus Hero"),
                ],
                default="",
            ),
        ),
        # Step 1: add nullable so we can fill per-row without collision
        migrations.AddField(
            model_name="user",
            name="referral_code",
            field=models.CharField(max_length=12, blank=True, default="", db_index=False),
        ),
        migrations.AddField(
            model_name="user",
            name="referral_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="user",
            name="referred_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="referrals",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # Step 2: assign a unique code to every existing row
        migrations.RunPython(assign_unique_referral_codes, migrations.RunPython.noop),
        # Step 3: add the unique constraint now that all values are distinct
        migrations.AlterField(
            model_name="user",
            name="referral_code",
            field=models.CharField(max_length=12, unique=True, db_index=True, default=""),
        ),
    ]
