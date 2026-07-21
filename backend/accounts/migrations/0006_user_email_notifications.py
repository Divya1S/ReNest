from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_campus_user_campus"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="email_notifications",
            field=models.BooleanField(default=True),
        ),
    ]
