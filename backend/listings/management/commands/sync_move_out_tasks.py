from django.core.management.base import BaseCommand

from listings.ops import sync_all_system_move_out_tasks


class Command(BaseCommand):
    help = "Sync system move-out task due dates with each scan session deadline."

    def handle(self, *args, **options):
        count = sync_all_system_move_out_tasks()
        self.stdout.write(self.style.SUCCESS(f"Synced {count} move-out task(s)."))
