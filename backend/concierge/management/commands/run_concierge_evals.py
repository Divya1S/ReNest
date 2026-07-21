from typing import Any

from django.core.management.base import BaseCommand, CommandError

from concierge import eval_runner


class Command(BaseCommand):
    help = (
        "Run the concierge golden-set evals. Offline checks (router, retrieval) "
        "always run; --live also runs full turns against the real Gemini API "
        "(needs GEMINI_API_KEY, costs tokens). Exits non-zero on any failure."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--live", action="store_true", help="Also run live model evals.")

    def handle(self, *args: Any, **options: Any) -> None:
        reports = [eval_runner.run_router_evals(), eval_runner.run_retrieval_evals()]

        if options["live"]:
            from concierge import engine

            if not engine.is_enabled():
                raise CommandError("--live requires GEMINI_API_KEY (and CONCIERGE_ENABLED).")
            from django.contrib.auth import get_user_model

            user, _ = get_user_model().objects.get_or_create(
                email="concierge-evals@renest.internal",
                defaults={"is_active": False},
            )
            reports.append(eval_runner.run_live_evals(user))

        failed = False
        for report in reports:
            status = self.style.SUCCESS("PASS") if report.ok else self.style.ERROR("FAIL")
            self.stdout.write(f"{status}  {report.name}: {report.passed}/{report.total}")
            for failure in report.failures:
                failed = True
                self.stdout.write(self.style.ERROR(f"       ✗ {failure}"))

        if failed:
            raise CommandError("Concierge evals failed.")
        self.stdout.write(self.style.SUCCESS("All concierge evals passed."))
