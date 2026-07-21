from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0024_listing_interaction_event"),
    ]

    operations = [
        migrations.AddField(
            model_name="reservation",
            name="pickup_slots",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Up to 5 ISO-8601 datetime strings proposed by the owner.",
            ),
        ),
        migrations.AddField(
            model_name="reservation",
            name="confirmed_slot",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="The slot the claimant confirmed from pickup_slots.",
            ),
        ),
        migrations.AddField(
            model_name="reservation",
            name="handoff_pin",
            field=models.CharField(
                blank=True,
                max_length=6,
                help_text="6-digit PIN shown to claimant; owner enters it at pickup to auto-complete.",
            ),
        ),
        migrations.AlterField(
            model_name="reservation",
            name="status",
            field=models.CharField(
                choices=[
                    ("requested", "Requested"),
                    ("confirmed", "Confirmed"),
                    ("cancelled", "Cancelled"),
                    ("completed", "Completed"),
                    ("expired_unresolved", "Expired Unresolved"),
                ],
                default="requested",
                max_length=24,
            ),
        ),
    ]
