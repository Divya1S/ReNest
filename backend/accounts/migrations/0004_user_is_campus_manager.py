from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_user_milestones_referrals"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="is_campus_manager",
            field=models.BooleanField(
                default=False,
                help_text="Grants access to the campus partner analytics dashboard.",
            ),
        ),
    ]
