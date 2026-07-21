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
    list_display = ("email", "display_name", "campus", "is_staff")
    search_fields = ("email", "display_name", "campus_name")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("display_name", "campus_name", "campus")}),
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
