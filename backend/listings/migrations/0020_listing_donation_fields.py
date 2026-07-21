from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("hubs", "0001_initial"),
        ("listings", "0019_reservationmessage"),
    ]

    operations = [
        migrations.AddField(
            model_name="listing",
            name="donation_hub",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="donated_listings",
                to="hubs.donationhub",
            ),
        ),
        migrations.AddField(
            model_name="listing",
            name="donation_receipt",
            field=models.FileField(blank=True, null=True, upload_to="donation-receipts/"),
        ),
    ]
