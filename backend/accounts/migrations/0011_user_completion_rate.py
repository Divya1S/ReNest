from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0010_campus_subscription_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="completion_rate",
            field=models.FloatField(
                blank=True,
                null=True,
                help_text="completed_reservations / total_reservations_as_owner. Recomputed weekly.",
            ),
        ),
    ]
