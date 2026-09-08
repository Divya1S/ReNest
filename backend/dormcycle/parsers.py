"""Request parsers shared by every API view."""

from __future__ import annotations

from typing import Any

from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser


class StrictJSONParser(JSONParser):
    """JSON parser that only accepts an object at the top level.

    Every ReNest endpoint reads ``request.data`` as a mapping. A bare array,
    string or number is valid JSON, so the stock parser would hand views a
    list and the first ``.get()`` would raise AttributeError (HTTP 500). Reject
    it up front as a normal 400 validation error instead.
    """

    def parse(self, stream: Any, media_type: Any = None, parser_context: Any = None) -> Any:
        data = super().parse(stream, media_type, parser_context)
        if not isinstance(data, dict):
            raise ParseError("Request body must be a JSON object.")
        return data
