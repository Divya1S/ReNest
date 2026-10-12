from __future__ import annotations

from django.core.management.base import BaseCommand

from listings.ops import (
    BUMP_EMAIL_BATCH_SIZE,
    BUMP_EMAIL_QUIET_DAYS,
    bump_email_candidates,
    send_stale_listing_bump_emails,
)


class Command(BaseCommand):
    help = (
        f"Email owners of available listings posted at least {BUMP_EMAIL_QUIET_DAYS} days ago "
        f"that have had no views in the last {BUMP_EMAIL_QUIET_DAYS} days, asking if the item "
        "is still available. Each listing is emailed at most once, owners who opted out of "
        f"email are skipped, and one run sends at most {BUMP_EMAIL_BATCH_SIZE}."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Print the listings the next real run would email, without sending "
                "anything or marking them as emailed."
            ),
        )

    def handle(self, *args, **options):
        if options["dry_run"]:
            # The same queryset and batch limit the task uses, so this is a
            # preview of the real run rather than a description of another rule.
            candidates = list(bump_email_candidates()[:BUMP_EMAIL_BATCH_SIZE])
            for listing in candidates:
                self.stdout.write(
                    f'[dry-run] Would bump listing #{listing.id}: "{listing.title}" '
                    f"(owner: {listing.owner.email})"
                )
            self.stdout.write(self.style.SUCCESS(f"Would bump {len(candidates)} listing(s)."))
            return

        sent = send_stale_listing_bump_emails()
        self.stdout.write(self.style.SUCCESS(f"Bumped {sent} listing(s)."))
