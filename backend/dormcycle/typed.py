"""Typing helpers shared across apps."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from accounts.models import User


def current_user(request: Any) -> User:
    """
    The request's authenticated user, typed as the concrete User model.

    Only call from views protected by IsAuthenticated (the project-wide
    default permission class); the cast is unsound for anonymous requests.
    """
    return cast("User", request.user)
