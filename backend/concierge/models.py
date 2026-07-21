from django.conf import settings
from django.db import models


class ConciergeThread(models.Model):
    """One persistent conversation per user with the Nest Concierge.

    The thread owns two memory layers: verbatim recent messages (rows in
    ConciergeMessage) and a rolling ``summary`` that older messages are folded
    into so the model's context window stays bounded on long conversations.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="concierge_thread",
    )
    summary = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"ConciergeThread<{self.user_id}>"


class ConciergeMessage(models.Model):
    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"

    thread = models.ForeignKey(
        ConciergeThread,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField(max_length=12, choices=Role.choices)
    content = models.TextField()
    # Names of tools the assistant invoked while producing this reply — kept for
    # transparency in the UI ("Checked live listings") and for debugging.
    used_tools = models.JSONField(default=list, blank=True)
    # True once this row has been folded into ConciergeThread.summary; folded
    # rows stay visible in history but are no longer sent to the model.
    folded = models.BooleanField(default=False)
    # Structured turn metadata: route, plan, reflection verdict, semantic-cache
    # flag, and server-hydrated generative-UI blocks the widget re-renders.
    meta = models.JSONField(default=dict, blank=True)
    # User feedback on assistant replies: +1 (helpful) / -1 (not helpful) /
    # null (unrated). The tuning signal for router exemplars and prompts.
    rating = models.SmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "id")
        indexes = [
            models.Index(fields=["thread", "created_at"], name="concierge_msg_thread_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.role}: {self.content[:40]}"


class KnowledgeChunk(models.Model):
    """A retrievable section of the product knowledge base.

    ``vector`` stores a hashed bag-of-terms embedding (see concierge.rag) — the
    same dependency-free technique the listing search uses. Swapping in a real
    embedding model later only requires replacing rag.embed() and reseeding.
    """

    slug = models.SlugField(max_length=80, unique=True)
    title = models.CharField(max_length=160)
    content = models.TextField()
    vector = models.JSONField(default=list, blank=True)
    # Which embedding space `vector` lives in (see rag.py). Vectors from
    # different spaces are never compared; when the remote embedding API
    # becomes available, hashed-space chunks are transparently re-seeded.
    vector_space = models.CharField(max_length=32, default="hashed-v1")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("slug",)

    def __str__(self) -> str:
        return self.title
