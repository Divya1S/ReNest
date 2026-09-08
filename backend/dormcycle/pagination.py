"""Project-wide pagination."""

from __future__ import annotations

from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    """Default page size of 24 with an opt-in override.

    Pages that render a user's complete set (My Listings, My Reservations) ask
    for a larger page rather than silently showing only the first 24 rows and
    computing their headline counters from that slice. The maximum keeps a
    crafted ?page_size= from turning a list endpoint into a full-table scan.
    """

    page_size = 24
    page_size_query_param = "page_size"
    max_page_size = 100
