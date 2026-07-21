from django.core.management.base import BaseCommand

from listings.ops import expire_stale_listings


class Command(BaseCommand):
    help = "Mark listings past their available_until deadline as expired. Run periodically via cron."

    def handle(self, *args, **options):
        count = expire_stale_listings()
        self.stdout.write(self.style.SUCCESS(f"Expired {count} listing(s)."))
