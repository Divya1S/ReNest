from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0026_listing_boost_organization_donationintent"),
    ]

    operations = [
        migrations.AddField(
            model_name="listing",
            name="quality_hints",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="AI-generated suggestions to improve listing quality. Shape: [{suggestion, field}].",
            ),
        ),
        migrations.AddField(
            model_name="listing",
            name="quality_hints_dismissed",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="DemandForecast",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("category", models.CharField(
                    choices=[
                        ("storage", "Storage"),
                        ("lighting", "Lighting"),
                        ("supplies", "School Supplies"),
                        ("comfort", "Comfort"),
                        ("toiletries", "Toiletries"),
                        ("decor", "Decor"),
                        ("other", "Other"),
                    ],
                    max_length=24,
                    db_index=True,
                )),
                ("week_number", models.PositiveSmallIntegerField(help_text="ISO week number (1–53) this forecast applies to.")),
                ("year", models.PositiveSmallIntegerField()),
                ("predicted_views", models.FloatField(default=0.0)),
                ("predicted_saves", models.FloatField(default=0.0)),
                ("is_peak", models.BooleanField(default=False, help_text="True when predicted_views >= 75th percentile for the category.")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ("-year", "-week_number", "category")},
        ),
        migrations.AddConstraint(
            model_name="demandforecast",
            constraint=models.UniqueConstraint(
                fields=("category", "week_number", "year"),
                name="unique_demand_forecast",
            ),
        ),
    ]
