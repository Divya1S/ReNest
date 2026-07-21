from typing import Any

from django.core.management.base import BaseCommand

from concierge.seeding import reseed_knowledge


class Command(BaseCommand):
    help = "Rebuild the concierge knowledge base from the bundled markdown."

    def handle(self, *args: Any, **options: Any) -> None:
        count = reseed_knowledge()
        self.stdout.write(self.style.SUCCESS(f"Seeded {count} knowledge chunks."))
