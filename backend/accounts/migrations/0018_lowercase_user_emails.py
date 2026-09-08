from django.db import migrations


def lowercase_emails(apps, schema_editor):
    """Canonicalise stored addresses to lower case.

    Accounts are matched case-insensitively from now on, so any address that
    differs only by case must be folded. When two rows would collide, the
    oldest keeps the canonical address and the newer one is suffixed and
    deactivated rather than deleted, so nothing is silently destroyed.
    """
    User = apps.get_model("accounts", "User")
    seen: dict[str, int] = {}
    for user in User.objects.order_by("id").iterator():
        lowered = (user.email or "").strip().lower()
        if not lowered:
            continue
        if lowered in seen:
            user.email = f"{lowered}.duplicate-{user.pk}"
            user.is_active = False
            user.save(update_fields=["email", "is_active"])
            continue
        seen[lowered] = user.pk
        if lowered != user.email:
            user.email = lowered
            user.save(update_fields=["email"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0017_campussubscription_unique_campus_subscription_session"),
    ]

    operations = [
        migrations.RunPython(lowercase_emails, migrations.RunPython.noop),
    ]
