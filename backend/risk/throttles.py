from __future__ import annotations

from listings.throttles import WriteOnlyUserRateThrottle


class PaymentCreateThrottle(WriteOnlyUserRateThrottle):
    scope = "risk_payment"


class SimulationThrottle(WriteOnlyUserRateThrottle):
    scope = "risk_simulation"


class InvestigationThrottle(WriteOnlyUserRateThrottle):
    scope = "risk_investigation"


class AgentAttemptThrottle(WriteOnlyUserRateThrottle):
    scope = "risk_agent"
