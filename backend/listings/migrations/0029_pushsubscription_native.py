from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0028_announcement"),
    ]

    operations = [
        migrations.AddField(
            model_name="pushsubscription",
            name="platform",
            field=models.CharField(
                choices=[("web", "Web (VAPID)"), ("apns", "iOS (APNs)"), ("fcm", "Android (FCM)")],
                db_index=True,
                default="web",
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name="pushsubscription",
            name="native_token",
            field=models.TextField(blank=True, db_index=True),
        ),
        migrations.AlterField(
            model_name="pushsubscription",
            name="p256dh",
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name="pushsubscription",
            name="auth",
            field=models.TextField(blank=True),
        ),
    ]
