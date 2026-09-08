from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Campus, User


@admin.register(Campus)
class CampusAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "timezone", "move_out_start", "move_out_end", "active")
    list_filter = ("active",)
    search_fields = ("name", "slug", "email_domains")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("email",)
    list_display = ("email", "display_name", "campus", "email_verified", "is_staff")
    list_filter = ("is_staff", "is_active", "email_verified", "is_campus_manager")
    search_fields = ("email", "display_name", "campus_name")
    # created_at is auto_now_add (non-editable): listing it in fieldsets without
    # declaring it read-only makes the change form raise FieldError.
    readonly_fields = ("created_at", "last_login")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("display_name", "campus_name", "campus", "email_verified")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "is_campus_manager",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "created_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "display_name", "campus_name", "campus", "password1", "password2"),
            },
        ),
    )
