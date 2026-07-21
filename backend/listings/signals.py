"""Model signals for the listings app."""
from __future__ import annotations

import logging
from typing import Any

from django.core.files.base import ContentFile
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Listing

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Listing, dispatch_uid="listing-generate-thumb")
def generate_listing_thumbnail(sender: Any, instance: Listing, **kwargs: Any) -> None:
    """
    Build a small card-grid rendition whenever a listing has a cover image but
    no thumbnail yet. Serializer.update() clears image_thumb when the cover is
    replaced, so this also regenerates after a swap. The guarded re-save with
    update_fields makes the recursive post_save a no-op.
    """
    if not instance.image or instance.image_thumb:
        return

    from .image_utils import make_thumbnail

    buf = make_thumbnail(instance.image)
    if buf is None:
        # Non-rasterisable source (e.g. seeded SVG) — card falls back to image_url.
        return

    stem = (instance.image.name or "cover").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    try:
        instance.image_thumb.save(f"{stem}-thumb.jpg", ContentFile(buf.getvalue()), save=False)
        instance.save(update_fields=["image_thumb"])
    except Exception:
        logger.exception("Thumbnail generation failed for listing %s", instance.pk)
