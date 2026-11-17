"""API error handling shared by every DRF view."""

from __future__ import annotations

from typing import Any, Optional

from django.http import Http404
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Optional[Response]:
    """DRF's handler, except that a missing object always reads "Not found.".

    DRF passes Django's Http404 message through to the client, and
    get_object_or_404 words that message around the model class
    ("No RoomScanSession matches the given query."). That names internal
    models in a public response and is what the SPA shows the user, since it
    renders `detail` as is.
    """
    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    return drf_exception_handler(exc, context)
