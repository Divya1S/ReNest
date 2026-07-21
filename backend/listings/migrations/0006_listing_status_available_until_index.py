from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0005_moveouttask"),
    ]

    operations = [
        migrations.AlterField(
            model_name="listing",
            name="status",
            field=models.CharField(
                choices=[
                    ("available", "Available"),
                    ("reserved", "Reserved"),
                    ("picked_up", "Picked Up"),
                    ("expired", "Expired"),
                    ("donated", "Donated"),
                ],
                db_index=True,
                default="available",
                max_length=24,
            ),
        ),
        migrations.AlterField(
            model_name="listing",
            name="available_until",
            field=models.DateTimeField(db_index=True),
        ),
    ]
