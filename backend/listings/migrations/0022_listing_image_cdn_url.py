from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0021_pushsubscription"),
    ]

    operations = [
        migrations.AddField(
            model_name="listing",
            name="image_cdn_url",
            field=models.URLField(blank=True, default="", max_length=500),
        ),
    ]
