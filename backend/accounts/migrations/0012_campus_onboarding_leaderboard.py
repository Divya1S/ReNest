from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_user_completion_rate"),
    ]

    operations = [
        # Campus onboarding fields (Phase 19)
        migrations.AddField(
            model_name="campus",
            name="onboarding_status",
            field=models.CharField(
                choices=[("pending", "Pending Approval"), ("approved", "Approved"), ("rejected", "Rejected")],
                db_index=True,
                default="approved",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="campus",
            name="contact_name",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="campus",
            name="contact_email",
            field=models.EmailField(blank=True),
        ),
        migrations.AddField(
            model_name="campus",
            name="last_digest_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="campus",
            name="annual_report",
            field=models.FileField(blank=True, null=True, upload_to="campus-reports/"),
        ),
        # User leaderboard opt-in (Phase 21)
        migrations.AddField(
            model_name="user",
            name="show_on_leaderboard",
            field=models.BooleanField(default=True),
        ),
    ]
