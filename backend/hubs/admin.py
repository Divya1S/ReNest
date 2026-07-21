from django.contrib import admin

from .models import DonationHub, HubManager


class HubManagerInline(admin.TabularInline):
    model = HubManager
    extra = 1
    autocomplete_fields = ["user"]


@admin.register(DonationHub)
class DonationHubAdmin(admin.ModelAdmin):
    list_display = ("name", "campus_name", "zone_label", "capacity", "active", "is_demo")
    list_filter = ("active", "is_demo", "campus_name")
    search_fields = ("name", "campus_name", "zone_label")
    inlines = [HubManagerInline]


@admin.register(HubManager)
class HubManagerAdmin(admin.ModelAdmin):
    list_display = ("hub", "user")
    search_fields = ("hub__name", "user__email", "user__display_name")
