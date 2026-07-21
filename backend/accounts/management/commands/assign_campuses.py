from typing import Any

from django.core.management.base import BaseCommand

from accounts.models import Campus, User


class Command(BaseCommand):
    help = (
        "Backfill User.campus for existing accounts by exact email-domain match. "
        "Safe to re-run; never overwrites an already-assigned campus."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        assigned = 0
        unmatched = 0
        for user in User.objects.filter(campus__isnull=True).iterator():
            campus = Campus.match_for_email(user.email)
            if campus is None:
                unmatched += 1
                continue
            user.campus = campus
            if not user.campus_name:
                user.campus_name = campus.name
                user.save(update_fields=["campus", "campus_name"])
            else:
                user.save(update_fields=["campus"])
            assigned += 1
        self.stdout.write(self.style.SUCCESS(f"Assigned {assigned}; no matching campus for {unmatched}."))
