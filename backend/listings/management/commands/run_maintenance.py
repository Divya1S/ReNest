from django.core.management.base import BaseCommand

from listings.ops import run_maintenance_cycle


class Command(BaseCommand):
    help = "Run the full ReNest maintenance suite: expiry, move-out task sync, and notifications."

    def handle(self, *args, **options):
        counts = run_maintenance_cycle()
        self.stdout.write(
            self.style.SUCCESS(
                "Maintenance complete "
                f"(expired={counts['expired']}, synced_tasks={counts['synced_tasks']}, "
                f"notifications_created={counts['created']}, notifications_updated={counts['updated']}, "
                f"notifications_active={counts['active']}, reminder_emails={counts['reminder_emails']})."
            )
        )
