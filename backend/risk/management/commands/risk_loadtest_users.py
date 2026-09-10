"""Create a pool of established sandbox customers for scripts/risk_loadtest.py.

    python manage.py risk_loadtest_users --count 24 --password 'loadtest-pass-123'

Each user is aged 200 days and given three completed sandbox payments from
one device, so the load test exercises the normal ALLOW path (evaluation,
events, audit, sandbox capture) instead of tripping the velocity rules the
way a single account firing hundreds of payments would. Never run this
against a production database.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from risk.models import PaymentTransaction
from risk.services.payments import PaymentInput, create_payment

EMAIL_PATTERN = "loadtest-{i}@renest.local"


class Command(BaseCommand):
    help = "Create load-test users with a little sandbox history (development only)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--count", type=int, default=24)
        parser.add_argument("--password", required=True)
        parser.add_argument("--pattern", default=EMAIL_PATTERN)

    def handle(self, *args: Any, **options: Any) -> None:
        user_model = get_user_model()
        created = 0
        for i in range(int(options["count"])):
            email = options["pattern"].format(i=i)
            user, was_created = user_model.objects.get_or_create(
                email=email, defaults={"display_name": f"Load test {i}", "email_verified": True}
            )
            user.set_password(options["password"])
            user.email_verified = True
            user.save()
            user_model.objects.filter(pk=user.pk).update(created_at=timezone.now() - timedelta(days=200))
            user.refresh_from_db()
            existing = PaymentTransaction.objects.filter(user=user, is_simulation=False).count()
            for n in range(max(0, 3 - existing)):
                create_payment(
                    user=user,
                    data=PaymentInput("mkt_textbooks", Decimal("24.00") + n, "USD", "card", f"load-{i}", merchant_category="books"),
                    request_id=f"loadtest-seed-{i}-{n}",
                    ip_prefix="10.30.0.0",
                )
            created += int(was_created)
        self.stdout.write(f"users ready: {options['count']} ({created} new), pattern {options['pattern']}")
