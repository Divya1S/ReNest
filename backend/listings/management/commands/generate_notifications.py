from django.core.management.base import BaseCommand

from listings.notifications import sync_notifications_for_all_users


class Command(BaseCommand):
    help = "Generate or refresh in-app notifications for all active users."

    def handle(self, *args, **options):
        counts = sync_notifications_for_all_users()
        self.stdout.write(
            self.style.SUCCESS(
                "Generated notifications "
                f"(created={counts['created']}, updated={counts['updated']}, active={counts['active']})."
            )
        )
