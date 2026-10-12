"""Seed a believable Risk Intelligence demo.

Creates real sandbox payments through the same code path as the API (so
events, evaluations and audit rows are genuine), one agent with a policy and
a few attempts, and three Fraud Lab runs (card testing, account takeover and
normal traffic).

    python manage.py seed_risk_demo --email demo@renest.test

What the payments look like, every time:

* `--payments` ordinary purchases (default 24) on the customer's usual device,
  two a day at 11:00 and 17:00 local time, ending two days ago. Each is
  evaluated as of its own timestamp, so they score as the unremarkable
  history they are instead of as two dozen payments made in one second.
* exactly four anomalies in the last day, in this order:
    1. one large purchase (far above the customer's average, new merchant)
    2. one purchase from a device the account has never used
    3. one payment the sandbox processor declines
    4. one card-testing burst: six sub-5.00 charges two minutes apart from
       another unfamiliar device

Nothing about the composition is random. `--seed` only varies the amounts and
merchants of the ordinary purchases, and the Fraud Lab data.

Two things the command does that a reader should know about:

* A history that starts before the account existed would be nonsense (and the
  engine would treat every row as a brand-new account), so an account younger
  than `--account-age-days` (default 120) has its creation date moved back.
  Pass `--account-age-days 0` to leave the account alone.
* Audit rows keep the time they were really written. Only the payments, their
  evaluations and their events carry the backdated time.

Running it twice for the same account does not double the history: payments
and agent attempts are seeded once.
"""

from __future__ import annotations

import random
import zoneinfo
from datetime import datetime, timedelta, tzinfo
from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from risk.models import AgentPolicy, PaymentTransaction, SimulationScenario
from risk.services.agents import evaluate_agent_transaction
from risk.services.payments import PaymentInput, create_payment
from risk.services.policy import current_sensitivity
from risk.services.simulation import run_scenario

# Where the ordinary purchases happen. The large purchase goes to a merchant
# that is deliberately not in this list, so it is also a first visit.
MERCHANTS = (
    ("mkt_textbooks", "books"),
    ("mkt_dorm_essentials", "home"),
    ("mkt_campus_cafe", "food"),
    ("mkt_bike_shop", "supplies"),
)
LARGE_PURCHASE_MERCHANT = ("mkt_electronics", "electronics")

USUAL_DEVICE = "demo-laptop"
NEW_DEVICE = "unknown-phone"
BURST_DEVICE = "kiosk-tablet"

# Ordinary amounts stay inside a band whose extremes are less than 2x apart,
# so no ordinary purchase can ever look like a jump against the average.
ORDINARY_MIN, ORDINARY_MAX = 18.0, 34.0
MIN_ORDINARY = 4  # the anomalies need an average and a known device to stand out against
BURST_SIZE = 6

SIMULATED_SCENARIOS = (SimulationScenario.CARD_TESTING, SimulationScenario.ACCOUNT_TAKEOVER, SimulationScenario.NORMAL)


def _local_zone(user: Any) -> tzinfo:
    name = getattr(getattr(user, "campus", None), "timezone", None)
    if name:
        try:
            return zoneinfo.ZoneInfo(str(name))
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            pass
    return timezone.get_default_timezone()


def ordinary_schedule(count: int, now: datetime, zone: tzinfo) -> list[datetime]:
    """Two purchases a day at 11:00 and 17:00 local, oldest first, the newest two days back."""
    today = now.astimezone(zone).replace(minute=0, second=0, microsecond=0)
    slots: list[datetime] = []
    for i in range(count):
        days_back = (count - 1 - i) // 2 + 2
        hour = 11 if (count - 1 - i) % 2 else 17
        slots.append((today - timedelta(days=days_back)).replace(hour=hour))
    return sorted(slots)


class Command(BaseCommand):
    help = "Seed sandbox payments, an agent policy and Fraud Lab runs for a demo user."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--email", required=True)
        parser.add_argument("--payments", type=int, default=24, help="Ordinary purchases to backdate (the four anomalies are added on top).")
        parser.add_argument("--seed", type=int, default=7)
        parser.add_argument("--account-age-days", type=int, default=120, help="Age the account to at least this many days; 0 leaves it untouched.")
        parser.add_argument("--skip-simulations", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        user_model = get_user_model()
        try:
            user = user_model.objects.get(email=options["email"].lower())
        except user_model.DoesNotExist as exc:
            raise CommandError(f"No user with email {options['email']}") from exc
        if options["payments"] < MIN_ORDINARY:
            raise CommandError(f"--payments must be at least {MIN_ORDINARY}: the anomalies need a baseline to stand out against.")

        if PaymentTransaction.objects.filter(user=user, is_simulation=False, metadata__seed="demo").exists():
            self.stdout.write("payments already seeded for this account; left as they are")
        else:
            self._age_account(user, options["account_age_days"])
            self._seed_payments(user, options["payments"], random.Random(options["seed"]))

        self._seed_agent(user)

        if not options["skip_simulations"]:
            sensitivity = current_sensitivity()
            for scenario in SIMULATED_SCENARIOS:
                run = run_scenario(user=user, scenario=scenario, sensitivity=sensitivity, count=1000, seed=options["seed"], request_id="seed-sim")
                self.stdout.write(f"simulation {scenario}: {run.public_id} blocked={run.metrics['blocked']} reviewed={run.metrics['reviewed']}")

    def _age_account(self, user: Any, days: int) -> None:
        if days <= 0:
            return
        wanted = timezone.now() - timedelta(days=days)
        if user.created_at <= wanted:
            return
        # created_at is auto_now_add, so it can only be moved with an UPDATE.
        type(user).objects.filter(pk=user.pk).update(created_at=wanted)
        user.refresh_from_db()
        self.stdout.write(f"account aged to {days} days so the seeded history fits inside its lifetime")

    def _pay(self, user: Any, index: int, at: datetime, profile: str, **fields: Any) -> PaymentTransaction:
        data = PaymentInput(
            currency="USD",
            metadata={"seed": "demo", "profile": profile},
            **fields,
        )
        return create_payment(user=user, data=data, request_id=f"seed-{index}", ip_prefix="10.20.0.0", now=at).transaction

    def _seed_payments(self, user: Any, ordinary: int, rng: random.Random) -> None:
        now = timezone.now()
        index = 0

        # Ordinary history first, oldest first, so every row is evaluated
        # against exactly the rows that precede it in time.
        for at in ordinary_schedule(ordinary, now, _local_zone(user)):
            merchant, category = rng.choice(MERCHANTS)
            self._pay(
                user, index, at, "ordinary",
                merchant_id=merchant,
                amount=Decimal(str(round(rng.uniform(ORDINARY_MIN, ORDINARY_MAX), 2))),
                payment_method=rng.choice(("card", "card", "wallet", "campus_credit")),
                device_id=USUAL_DEVICE,
                merchant_category=category,
                # A few show a retried processor call on their timeline; they still complete.
                sandbox_behavior="fail_transient_once" if index % 9 == 4 else "succeed",
            )
            index += 1

        # The four anomalies, fixed and in chronological order.
        merchant, category = LARGE_PURCHASE_MERCHANT
        self._pay(
            user, index, now - timedelta(hours=20), "large_purchase",
            merchant_id=merchant, amount=Decimal("2400.00"), payment_method="card",
            device_id=USUAL_DEVICE, merchant_category=category,
        )
        index += 1
        self._pay(
            user, index, now - timedelta(hours=9), "new_device",
            merchant_id="mkt_textbooks", amount=Decimal("36.00"), payment_method="card",
            device_id=NEW_DEVICE, merchant_category="books",
        )
        index += 1
        self._pay(
            user, index, now - timedelta(hours=4), "processor_decline",
            merchant_id="mkt_campus_cafe", amount=Decimal("27.50"), payment_method="card",
            device_id=USUAL_DEVICE, merchant_category="food", sandbox_behavior="decline",
        )
        index += 1
        burst_start = now - timedelta(minutes=66)
        for n in range(BURST_SIZE):
            self._pay(
                user, index, burst_start + timedelta(minutes=2 * n), "card_testing_burst",
                merchant_id="mkt_dorm_essentials", amount=Decimal("1.00") + Decimal("0.25") * n, payment_method="card",
                device_id=BURST_DEVICE, merchant_category="home",
            )
            index += 1

        self.stdout.write(
            f"payments created: {index} ({ordinary} ordinary, 1 large purchase, 1 new-device purchase, "
            f"1 processor decline, 1 card-testing burst of {BURST_SIZE})"
        )

    def _seed_agent(self, user: Any) -> None:
        agent, created = AgentPolicy.objects.get_or_create(
            user=user,
            name="Dorm restock agent",
            defaults={
                "daily_limit": Decimal("150"),
                "transaction_limit": Decimal("60"),
                "requires_approval_above": Decimal("40"),
                "allowed_categories": ["supplies", "food", "books"],
                "blocked_merchants": ["mkt_tickets"],
            },
        )
        if created:
            # One allowed, one held for approval, one over the limits, one blocked merchant.
            attempts = (
                (Decimal("18.50"), "supplies", "mkt_supplies"),
                (Decimal("52"), "books", "mkt_textbooks"),
                (Decimal("75"), "electronics", "mkt_electronics"),
                (Decimal("30"), "food", "mkt_tickets"),
            )
            for amount, category, merchant in attempts:
                evaluate_agent_transaction(agent=agent, amount=amount, currency="USD", category=category, merchant_id=merchant, actor=user, request_id="seed-agent")
        self.stdout.write(f"agent ready: {agent.public_id}")
