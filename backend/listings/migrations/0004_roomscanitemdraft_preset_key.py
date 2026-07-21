from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0003_roomscanimage_listing_estimated_retail_value_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="roomscanitemdraft",
            name="preset_key",
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
    ]
