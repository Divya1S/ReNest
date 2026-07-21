from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0018_listing_building"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ReservationMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "reservation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="messages",
                        to="listings.reservation",
                    ),
                ),
                (
                    "sender",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reservation_messages",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("body", models.TextField(max_length=1000)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ("created_at",),
            },
        ),
        migrations.AddIndex(
            model_name="reservationmessage",
            index=models.Index(
                fields=["reservation", "created_at"],
                name="resmsg_reservation_time_idx",
            ),
        ),
    ]
