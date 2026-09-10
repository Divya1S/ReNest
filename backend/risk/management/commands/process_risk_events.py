"""Dispatch pending risk events, purge expired idempotency keys and re-drive
approved payments that never reached the processor.

    python manage.py process_risk_events            one pass
    python manage.py process_risk_events --loop 5   every 5 seconds until interrupted

The maintenance tick (listings.ops) runs the same work on a schedule; this
command is for local demos and for a dedicated worker process.
"""

from __future__ import annotations

import time
from typing import Any

from django.core.management.base import BaseCommand

from risk.services.events import dispatch_pending
from risk.services.idempotency import purge_expired
from risk.services.payments import reconcile_uncaptured


class Command(BaseCommand):
    help = "Process pending risk events (and purge expired idempotency keys)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--loop", type=float, default=0, help="Seconds between passes; 0 runs once.")
        parser.add_argument("--limit", type=int, default=200)

    def handle(self, *args: Any, **options: Any) -> None:
        interval = float(options["loop"])
        while True:
            summary = dispatch_pending(limit=int(options["limit"]))
            purged = purge_expired()
            reconciled = reconcile_uncaptured()
            self.stdout.write(
                f"processed={summary['processed']} failed={summary['failed']} purged_keys={purged} "
                f"reconciled={reconciled['completed']}+{reconciled['failed']}"
            )
            if interval <= 0:
                return
            time.sleep(interval)
