"""Parse the bundled knowledge markdown into embedded KnowledgeChunk rows."""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from django.db import transaction
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


@dataclass(frozen=True)
class ParsedKnowledge:
    """The bundled knowledge file, parsed once per file version."""

    sections: tuple[tuple[str, str], ...]        # (title, body) in file order
    by_slug: dict[str, tuple[str, str]]          # slug -> (title, body)
    version: str                                 # content digest; changes with any edit


# ensure_seeded() runs on every chat request. Reading and regex-splitting the
# markdown each time is wasted work for a file that changes only on deploy, so
# the parse is kept per process and keyed on the file's identity on disk.
_parse_lock = threading.Lock()
_parsed: tuple[tuple[str, int, int], ParsedKnowledge] | None = None
# One reseed at a time per process: without this, two first requests both pay
# for embedding the whole corpus.
_reseed_lock = threading.Lock()


def load_knowledge() -> ParsedKnowledge:
    """Parsed knowledge file, re-read only when its mtime or size changes."""
    global _parsed
    stat = KNOWLEDGE_PATH.stat()
    key = (str(KNOWLEDGE_PATH), stat.st_mtime_ns, stat.st_size)
    cached = _parsed
    if cached is not None and cached[0] == key:
        return cached[1]
    with _parse_lock:
        cached = _parsed
        if cached is not None and cached[0] == key:
            return cached[1]
        markdown = KNOWLEDGE_PATH.read_text(encoding="utf-8")
        sections = tuple(parse_sections(markdown))
        parsed = ParsedKnowledge(
            sections=sections,
            by_slug={slugify(title)[:80]: (title, body) for title, body in sections},
            version=hashlib.sha256(markdown.encode("utf-8")).hexdigest()[:16],
        )
        _parsed = (key, parsed)
        return parsed


def knowledge_version() -> str:
    """Digest of the bundled knowledge file. Cached answers are keyed on it."""
    return load_knowledge().version


def reseed_knowledge() -> int:
    """Rebuild the KnowledgeChunk table from the bundled markdown. Returns count.

    Written as an upsert plus a delete of stale slugs inside one transaction.
    The earlier delete-everything-then-insert left a window in which a reader
    saw an empty knowledge base, and two first requests racing through it
    collided on the unique slug.
    """
    knowledge = load_knowledge()
    sections = knowledge.sections
    # Title terms doubled so heading words ("handoff PIN", "pricing") outrank
    # incidental matches in long generic sections (hashed space); harmless in
    # the remote space. Embedding may call the network, so it stays outside
    # the transaction.
    vectors, space = rag.embed_documents([f"{title}\n{title}\n{body}" for title, body in sections])
    # Keyed by slug so two headings that slugify alike cannot put the same
    # conflict target into one statement (the later section wins, as it does
    # in ParsedKnowledge.by_slug).
    by_slug = {
        slugify(title)[:80]: KnowledgeChunk(
            slug=slugify(title)[:80],
            title=title,
            content=body,
            vector=vector,
            vector_space=space,
        )
        for (title, body), vector in zip(sections, vectors)
    }
    chunks = list(by_slug.values())
    with transaction.atomic():
        # INSERT ... ON CONFLICT (slug) DO UPDATE: a concurrent seeder that
        # already inserted the same slug turns into an update, not an error.
        KnowledgeChunk.objects.bulk_create(
            chunks,
            update_conflicts=True,
            unique_fields=["slug"],
            update_fields=["title", "content", "vector", "vector_space", "updated_at"],
        )
        KnowledgeChunk.objects.exclude(slug__in=[chunk.slug for chunk in chunks]).delete()
    return len(chunks)


def _reseed_reason() -> str | None:
    """Why the stored corpus needs rebuilding, or None when it is current."""
    knowledge = load_knowledge()
    rows = list(KnowledgeChunk.objects.values_list("slug", "title", "content", "vector_space"))
    if not rows:
        return "empty"
    # Compare content, not just the slug set: an edit to a section body keeps
    # every slug and used to be ignored until someone reseeded by hand.
    if {slug: (title, content) for slug, title, content, _ in rows} != knowledge.by_slug:
        return "stored corpus differs from the bundled file"
    if all(space == rag.SPACE_HASHED for *_, space in rows) and rag.remote_available():
        return "upgrade to remote embedding space"
    return None


def ensure_seeded() -> None:
    """Seed the knowledge base on first use so the concierge works zero-config.

    Also reseeds when (a) the bundled markdown changed (deploys pick up KB
    edits without a manual command), or (b) a hashed-space corpus can be
    upgraded to real semantic embeddings now that the API key is available.
    Writes only concierge-owned tables; safe to call from the request path:
    one small query when nothing changed.
    """
    if _reseed_reason() is None:
        return
    with _reseed_lock:
        # Another thread may have finished the same reseed while this one waited.
        reason = _reseed_reason()
        if reason is None:
            return
        if reason != "empty":
            logger.info("concierge knowledge base reseeding: %s", reason)
        reseed_knowledge()
