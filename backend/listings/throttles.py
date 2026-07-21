from __future__ import annotations

from rest_framework.permissions import SAFE_METHODS
from rest_framework.request import Request
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView


class WriteOnlyUserRateThrottle(UserRateThrottle):
    """Rate-limit mutating requests only.

    DRF applies view-level throttles to every HTTP method, so on a combined
    list+create view a quota meant for POST (e.g. 20 listing creations/hour)
    would otherwise be drained by plain browsing GETs — locking users out of
    reading the marketplace for the rest of the window.
    """

    def allow_request(self, request: Request, view: APIView) -> bool:
        if request.method in SAFE_METHODS:
            return True
        return super().allow_request(request, view)


class ListingCreateThrottle(WriteOnlyUserRateThrottle):
    scope = "listing_create"


class AiDetectThrottle(WriteOnlyUserRateThrottle):
    scope = "ai_detect"


class ReservationCreateThrottle(WriteOnlyUserRateThrottle):
    scope = "reservation_create"


class PushSubscribeThrottle(WriteOnlyUserRateThrottle):
    scope = "push_subscribe"
