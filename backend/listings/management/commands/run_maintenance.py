from typing import Any

from django.core.management.base import BaseCommand, CommandError

from listings.ops import SCHEDULED_JOB_SETS, run_scheduled_jobs


class Command(BaseCommand):
    help = (
        "Run the scheduled maintenance jobs in-process (no Celery beat needed): "
        "listing expiry, move-out task sync, notifications, reminder and bump "
        "emails, stale-confirmation sweep, saved-search alerts, trending cache "
        "and push-queue flush. Use --jobs to pick the daily/weekly sets too."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--jobs",
            default="tick",
            help=(
                "Comma-separated job sets to run: "
                + ", ".join(SCHEDULED_JOB_SETS)
                + ", or all (default: tick)."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        requested = [item.strip() for item in options["jobs"].split(",") if item.strip()]
        unknown = [item for item in requested if item != "all" and item not in SCHEDULED_JOB_SETS]
        if not requested or unknown:
            raise CommandError(f"Unknown job set(s): {', '.join(unknown) or '(none given)'}")

        results = run_scheduled_jobs(requested)
        failed = [name for name, result in results.items() if not result["ok"]]
        for name, result in results.items():
            if result["ok"]:
                self.stdout.write(f"  ok    {name}: {result['result']}")
            else:
                self.stdout.write(self.style.ERROR(f"  FAIL  {name}: {result['error']}"))
        if failed:
            raise CommandError(f"{len(failed)} maintenance job(s) failed: {', '.join(failed)}")
        self.stdout.write(self.style.SUCCESS(f"Maintenance complete ({len(results)} jobs)."))
