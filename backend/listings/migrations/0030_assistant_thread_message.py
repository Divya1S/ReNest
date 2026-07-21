import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0013_user_assistant_notes"),
        ("listings", "0029_pushsubscription_native"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AssistantThread",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="assistant_threads",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("thread_id", models.CharField(db_index=True, max_length=64, unique=True)),
                ("summary", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ("-updated_at",)},
        ),
        migrations.CreateModel(
            name="AssistantMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "thread",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="messages",
                        to="listings.assistantthread",
                    ),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[("user", "User"), ("assistant", "Assistant"), ("tool_result", "Tool Result")],
                        max_length=16,
                    ),
                ),
                ("content", models.TextField()),
                ("tool_name", models.CharField(blank=True, max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ("created_at",)},
        ),
    ]
