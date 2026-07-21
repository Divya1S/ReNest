from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0012_campus_onboarding_leaderboard"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="assistant_notes",
            field=models.TextField(
                blank=True,
                max_length=1000,
                help_text="Short memory summary carried across assistant sessions.",
            ),
        ),
    ]
