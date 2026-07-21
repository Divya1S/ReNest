from __future__ import annotations

from django.core.management.base import BaseCommand

from listings.models import Listing
from listings.ops import send_stale_listing_bump_emails


class Command(BaseCommand):
    help = (
        "Email owners of active listings that have had zero new view events "
        "in the last 3 days asking if the item is still available. "
        "Each listing is emailed at most once every 7 days."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print which listings would be bumped without sending emails.",
        )

    def handle(self, *args, **options):
        if options["dry_run"]:
            from datetime import timedelta

            from django.utils import timezone

            now = timezone.now()
            inactive_cutoff = now - timedelta(days=3)
            resend_cooldown = now - timedelta(days=7)

            candidates = (
                Listing.objects.select_related("owner")
                .filter(
                    status__in=[Listing.Status.AVAILABLE, Listing.Status.RESERVED],
                    is_demo=False,
                    available_until__gt=now,
                    updated_at__lt=inactive_cutoff,
                )
                .exclude(bump_emailed_at__gt=resend_cooldown)
                .exclude(view_events__viewed_at__gt=inactive_cutoff)
                .distinct()
            )
            count = candidates.count()
            for listing in candidates:
                self.stdout.write(
                    f'[dry-run] Would bump listing #{listing.id}: "{listing.title}" '
                    f"(owner: {listing.owner.email})"
                )
            self.stdout.write(self.style.SUCCESS(f"Would bump {count} listing(s)."))
            return

        sent = send_stale_listing_bump_emails()
        self.stdout.write(self.style.SUCCESS(f"Bumped {sent} listing(s)."))
