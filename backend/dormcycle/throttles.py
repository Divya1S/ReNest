"""Throttles shared across apps."""

from __future__ import annotations

from typing import Optional

from rest_framework.request import Request
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView


class ClientIpRateThrottle(SimpleRateThrottle):
    """Rate limit by client address, whether or not the caller is signed in.

    DRF's AnonRateThrottle exempts authenticated requests. On the auth
    endpoints that made the limit optional: anyone holding a session (a
    throwaway account is enough) could guess another account's password or
    trigger reset emails as fast as they liked.
    """

    def get_cache_key(self, request: Request, view: APIView) -> Optional[str]:
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}
