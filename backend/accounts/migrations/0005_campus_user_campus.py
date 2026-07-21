import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_user_is_campus_manager"),
    ]

    operations = [
        migrations.CreateModel(
            name="Campus",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=200)),
                ("slug", models.SlugField(max_length=60, unique=True)),
                ("email_domains", models.CharField(blank=True, default="", help_text="Comma-separated list of .edu domains for this campus.", max_length=500)),
                ("timezone", models.CharField(default="America/Los_Angeles", max_length=60)),
                ("move_out_start", models.DateField(blank=True, null=True)),
                ("move_out_end", models.DateField(blank=True, null=True)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name_plural": "campuses",
                "ordering": ["name"],
            },
        ),
        migrations.AddField(
            model_name="user",
            name="campus",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="members",
                to="accounts.campus",
            ),
        ),
    ]
