from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0025_reservation_handoff_coordination"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="listing",
            name="boosted_until",
            field=models.DateTimeField(
                blank=True,
                null=True,
                db_index=True,
                help_text="Listings boosted via Stripe tip surface first in browse until this datetime.",
            ),
        ),
        migrations.CreateModel(
            name="Organization",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=200)),
                ("cause_url", models.URLField(blank=True)),
                ("campus_name", models.CharField(blank=True, max_length=120)),
                ("venmo_handle", models.CharField(blank=True, max_length=80)),
                ("verified", models.BooleanField(default=False, db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ("name",)},
        ),
        migrations.CreateModel(
            name="DonationIntent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("listing", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="donation_intents", to="listings.listing")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="donation_intents", to="listings.organization")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="donation_intents", to=settings.AUTH_USER_MODEL)),
                ("percentage", models.PositiveSmallIntegerField(default=10)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ("-created_at",)},
        ),
    ]
