from __future__ import annotations

from rest_framework.permissions import SAFE_METHODS
from rest_framework.request import Request
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView


class WriteOnlyUserRateThrottle(UserRateThrottle):
    """Rate-limit mutating requests only.

    DRF applies view-level throttles to every HTTP method, so on a combined
    list+create view a quota meant for POST (e.g. 20 listing creations/hour)
    would otherwise be drained by plain browsing GETs, locking users out of
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


class AiAssistThrottle(UserRateThrottle):
    """Per-user budget for endpoints that call the language model.

    Two differences from the throttles above. It counts every HTTP method:
    rescue-suggestions makes a model call inside a GET, which a write-only
    throttle would never see. And `rate` is set here on the class, so the
    limit does not depend on a DEFAULT_THROTTLE_RATES entry being added to
    settings; DRF only looks the scope up there when `rate` is missing.

    Each endpoint gets its own scope (and so its own counter) so that typing
    in the search box cannot use up the budget for describing a listing.
    """

    scope = "ai_assist"
    rate = "30/hour"


class ParseSearchThrottle(AiAssistThrottle):
    scope = "ai_parse_search"
    rate = "60/hour"


class GenerateDescriptionThrottle(AiAssistThrottle):
    scope = "ai_generate_description"
    rate = "30/hour"


class VisionAutofillThrottle(AiAssistThrottle):
    scope = "ai_vision_autofill"
    rate = "20/hour"


class RescueSuggestionsThrottle(AiAssistThrottle):
    scope = "ai_rescue_suggestions"
    rate = "20/hour"
