from django.contrib import admin

from .models import ConciergeMessage, ConciergeThread, ConciergeTurnOutcome, KnowledgeChunk


@admin.register(ConciergeThread)
class ConciergeThreadAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "updated_at")
    search_fields = ("user__email",)


@admin.register(ConciergeMessage)
class ConciergeMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "thread", "role", "folded", "created_at")
    list_filter = ("role", "folded")


@admin.register(KnowledgeChunk)
class KnowledgeChunkAdmin(admin.ModelAdmin):
    list_display = ("slug", "title", "updated_at")
    search_fields = ("title", "content")


@admin.register(ConciergeTurnOutcome)
class ConciergeTurnOutcomeAdmin(admin.ModelAdmin):
    list_display = ("day", "outcome", "count")
    list_filter = ("outcome",)
    date_hierarchy = "day"
