"""Parse the bundled knowledge markdown into embedded KnowledgeChunk rows."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from django.utils.text import slugify

from . import rag
from .models import KnowledgeChunk

logger = logging.getLogger(__name__)

KNOWLEDGE_PATH = Path(__file__).resolve().parent / "knowledge" / "renest_help.md"

_SECTION_RE = re.compile(r"^## +(.+?)\s*$", re.MULTILINE)


def parse_sections(markdown: str) -> list[tuple[str, str]]:
    """Split a knowledge doc into (title, body) pairs, one per '## ' heading."""
    sections: list[tuple[str, str]] = []
    matches = list(_SECTION_RE.finditer(markdown))
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        body = markdown[start:end].strip()
        if body:
            sections.append((match.group(1).strip(), body))
    return sections


def reseed_knowledge() -> int:
    """Rebuild the KnowledgeChunk table from the bundled markdown. Returns count."""
    sections = parse_sections(KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    # Title terms doubled so heading words ("handoff PIN", "pricing") outrank
    # incidental matches in long generic sections (hashed space); harmless in
    # the remote space.
    vectors, space = rag.embed_documents([f"{title}\n{title}\n{body}" for title, body in sections])
    KnowledgeChunk.objects.all().delete()
    KnowledgeChunk.objects.bulk_create(
        KnowledgeChunk(
            slug=slugify(title)[:80],
            title=title,
            content=body,
            vector=vector,
            vector_space=space,
        )
        for (title, body), vector in zip(sections, vectors)
    )
    return len(sections)


def _expected_slugs() -> set[str]:
    sections = parse_sections(KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    return {slugify(title)[:80] for title, _ in sections}


def ensure_seeded() -> None:
    """Seed the knowledge base on first use so the concierge works zero-config.

    Also reseeds when (a) the bundled markdown changed (slug set drifted from
    the stored corpus — deploys pick up KB edits without a manual command),
    or (b) a hashed-space corpus can be upgraded to real semantic embeddings
    now that the API key is available. Writes only concierge-owned tables;
    safe to call from the request path.
    """
    first = KnowledgeChunk.objects.only("vector_space").first()
    if first is None:
        reseed_knowledge()
        return
    if set(KnowledgeChunk.objects.values_list("slug", flat=True)) != _expected_slugs():
        logger.info("concierge knowledge base changed on disk; reseeding")
        reseed_knowledge()
        return
    if first.vector_space == rag.SPACE_HASHED and rag.remote_available():
        logger.info("upgrading concierge knowledge base to remote embedding space")
        reseed_knowledge()
