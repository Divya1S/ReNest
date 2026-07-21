from django.urls import path

from .views import (
    ConciergeChatStreamView,
    ConciergeChatView,
    ConciergeFeedbackView,
    ConciergeHistoryView,
    ConciergeResetView,
    ConciergeStatsView,
)

urlpatterns = [
    path("chat/", ConciergeChatView.as_view(), name="concierge-chat"),
    path("chat/stream/", ConciergeChatStreamView.as_view(), name="concierge-chat-stream"),
    path("history/", ConciergeHistoryView.as_view(), name="concierge-history"),
    path("reset/", ConciergeResetView.as_view(), name="concierge-reset"),
    path("messages/<int:pk>/feedback/", ConciergeFeedbackView.as_view(), name="concierge-feedback"),
    path("stats/", ConciergeStatsView.as_view(), name="concierge-stats"),
]
