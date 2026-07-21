from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0009_user_onboarding_step"),
    ]

    operations = [
        migrations.AddField(
            model_name="campus",
            name="subscription_tier",
            field=models.CharField(
                choices=[
                    ("free", "Free"),
                    ("standard", "Standard ($199/semester)"),
                    ("premium", "Premium ($399/semester)"),
                ],
                default="free",
                max_length=16,
                db_index=True,
            ),
        ),
        migrations.AddField(
            model_name="campus",
            name="license_expires_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="When the current paid license expires. Null = free tier.",
            ),
        ),
        migrations.AddField(
            model_name="campus",
            name="billing_email",
            field=models.EmailField(
                blank=True,
                help_text="Invoice recipient for campus license billing.",
            ),
        ),
        migrations.AddField(
            model_name="campus",
            name="stripe_customer_id",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.CreateModel(
            name="CampusSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="subscriptions", to="accounts.campus")),
                ("stripe_session_id", models.CharField(blank=True, max_length=120)),
                ("tier", models.CharField(max_length=16)),
                ("amount_cents", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ("-created_at",)},
        ),
    ]
