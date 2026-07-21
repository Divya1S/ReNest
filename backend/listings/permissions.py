from rest_framework import permissions
from typing import Any


class IsEmailVerified(permissions.BasePermission):
    message = "You must verify your email address before posting listings."

    def has_permission(self, request: Any, view: Any) -> bool:
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user.is_authenticated and request.user.email_verified


class IsListingOwnerOrReadOnly(permissions.BasePermission):
    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        if request.method in permissions.SAFE_METHODS:
            return True
        return obj.owner_id == request.user.id


class IsReservationParticipant(permissions.BasePermission):
    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        return obj.claimant_id == request.user.id or obj.listing.owner_id == request.user.id


class IsRescueRequestOwnerOrReadOnly(permissions.BasePermission):
    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        if request.method in permissions.SAFE_METHODS:
            return True
        return obj.seeker_id == request.user.id
