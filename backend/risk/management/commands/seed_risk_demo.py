"""Seed a believable Risk Intelligence demo.

Creates real sandbox payments through the same code path as the API (so
events, evaluations and audit rows are genuine), one agent with a policy and
a few attempts, and one Fraud Lab run per scenario.

    python manage.py seed_risk_demo --email demo@renest.test
"""

from __future__ import annotations

import random
from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from risk.models import SimulationScenario
from risk.services.agents import evaluate_agent_transaction
from risk.services.payments import PaymentInput, create_payment
from risk.services.policy import current_sensitivity
from risk.services.simulation import run_scenario
from risk.models import AgentPolicy

MERCHANTS = ("mkt_textbooks", "mkt_dorm_essentials", "mkt_campus_cafe", "mkt_electronics", "mkt_bike_shop")


class Command(BaseCommand):
    help = "Seed sandbox payments, an agent policy and Fraud Lab runs for a demo user."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--email", required=True)
        parser.add_argument("--payments", type=int, default=24)
        parser.add_argument("--seed", type=int, default=7)
        parser.add_argument("--skip-simulations", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        user_model = get_user_model()
        try:
            user = user_model.objects.get(email=options["email"].lower())
        except user_model.DoesNotExist as exc:
            raise CommandError(f"No user with email {options['email']}") from exc

        rng = random.Random(options["seed"])
        device = "demo-laptop"
        created = 0
        # Ordinary history first so later anomalies have a baseline.
        for i in range(options["payments"]):
            anomaly = i >= options["payments"] - 4
            if anomaly:
                choice = rng.choice(("big", "new_device", "burst", "decline"))
                amount = Decimal(str(rng.choice((480, 950, 1800)))) if choice == "big" else Decimal(str(round(rng.uniform(8, 60), 2)))
                data = PaymentInput(
                    merchant_id=rng.choice(MERCHANTS),
                    amount=Decimal("2.50") if choice == "burst" else amount,
                    currency="USD",
                    payment_method="card",
                    device_id="unknown-phone" if choice == "new_device" else device,
                    merchant_category="electronics" if choice == "big" else "supplies",
                    sandbox_behavior="decline" if choice == "decline" else "succeed",
                    metadata={"seed": "demo", "profile": choice},
                )
            else:
                data = PaymentInput(
                    merchant_id=rng.choice(MERCHANTS),
                    amount=Decimal(str(round(rng.lognormvariate(3.2, 0.5), 2))),
                    currency="USD",
                    payment_method=rng.choice(("card", "card", "wallet", "campus_credit")),
                    device_id=device,
                    merchant_category=rng.choice(("books", "home", "food", "supplies")),
                    sandbox_behavior="fail_transient_once" if i % 9 == 4 else "succeed",
                    metadata={"seed": "demo"},
                )
            create_payment(user=user, data=data, request_id=f"seed-{i}", ip_prefix="10.20.0.0")
            created += 1
        self.stdout.write(f"payments created: {created}")

        agent, _ = AgentPolicy.objects.get_or_create(
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
        for amount, category, merchant in ((Decimal("18.50"), "supplies", "mkt_supplies"), (Decimal("52"), "books", "mkt_textbooks"), (Decimal("75"), "electronics", "mkt_electronics"), (Decimal("30"), "food", "mkt_tickets")):
            evaluate_agent_transaction(agent=agent, amount=amount, currency="USD", category=category, merchant_id=merchant, actor=user, request_id="seed-agent")
        self.stdout.write(f"agent ready: {agent.public_id}")

        if not options["skip_simulations"]:
            sensitivity = current_sensitivity()
            for scenario in (SimulationScenario.CARD_TESTING, SimulationScenario.ACCOUNT_TAKEOVER, SimulationScenario.NORMAL):
                run = run_scenario(user=user, scenario=scenario, sensitivity=sensitivity, count=1000, seed=options["seed"], request_id="seed-sim")
                self.stdout.write(f"simulation {scenario}: {run.public_id} blocked={run.metrics['blocked']} reviewed={run.metrics['reviewed']}")
