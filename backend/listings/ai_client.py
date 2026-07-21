"""One Google Gemini client for every marketplace AI feature.

Scan detection, listing moderation, rescue-request matching, category
suggestions, and listing copywriting all go through these helpers, so the
whole platform shares one provider, one availability check
(GEMINI_API_KEY / GOOGLE_API_KEY), and one JSON-parsing contract. The
concierge app has its own engine (it needs tool loops and a circuit breaker);
both speak to the same Gemini account.

Design rules:
  * Callers own their failure semantics (skip / 503 / raise) — helpers raise
    and callers catch, exactly like the previous per-feature clients did.
  * JSON helpers request `application/json` output, then parse defensively
    anyway (fences, stray prose) — belt and braces.
  * Utility calls run on the fast model with thinking disabled: these are
    sub-second classification jobs, not reasoning tasks.
"""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

from django.conf import settings

MAX_IMAGE_BYTES = 8 * 1024 * 1024
_KEY_ENV_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")


class AiNotConfigured(RuntimeError):
    """No Gemini key present. Subclasses RuntimeError so existing
    `except RuntimeError` → 503 handling keeps working unchanged."""


def ai_available() -> bool:
    return any(os.getenv(var, "").strip() for var in _KEY_ENV_VARS)


def _require_key() -> None:
    if not ai_available():
        raise AiNotConfigured("GEMINI_API_KEY is not configured.")


_CACHED_CLIENT: Any = None


def _client() -> Any:
    """Process-lifetime Gemini client.

    Must be cached: genai.Client closes its httpx connections in __del__, so
    the transient `_client().models.generate_content(...)` pattern lets the
    Client be garbage-collected mid-call (only `.models` stays referenced) and
    every request dies with "Cannot send a request, as the client has been
    closed."
    """
    global _CACHED_CLIENT
    if _CACHED_CLIENT is None:
        from google import genai

        _CACHED_CLIENT = genai.Client(http_options={"timeout": 30_000})  # ms
    return _CACHED_CLIENT


def _fast_model() -> str:
    return getattr(settings, "AI_MODEL_FAST", "gemini-flash-latest")


def _vision_model() -> str:
    return getattr(settings, "AI_MODEL_VISION", "gemini-flash-latest")


def _utility_config(max_tokens: int, model: str, want_json: bool) -> dict[str, Any]:
    # Thinking tokens count toward max_output_tokens on current Gemini
    # models — reserve headroom so low-level thinking can't starve the
    # visible answer down to an empty string.
    config: dict[str, Any] = {"max_output_tokens": max_tokens + 256}
    if want_json:
        config["response_mime_type"] = "application/json"
    # Utility calls shouldn't spend thinking tokens. Current Gemini models
    # take thinking_level (low|high); the old zero thinking_budget is
    # rejected with 400 INVALID_ARGUMENT. `_generate` strips the config and
    # retries if a pinned older model rejects thinking_level instead.
    config["thinking_config"] = {"thinking_level": "low"}
    return config


def _generate(model: str, contents: list[Any], config: dict[str, Any]) -> Any:
    """generate_content with a one-shot retry sans thinking_config on 400."""
    try:
        return _client().models.generate_content(
            model=model, contents=contents, config=config
        )
    except Exception as exc:
        if getattr(exc, "code", None) == 400 and "thinking_config" in config:
            stripped = {k: v for k, v in config.items() if k != "thinking_config"}
            return _client().models.generate_content(
                model=model, contents=contents, config=stripped
            )
        raise


def _hit_token_cap(response: Any) -> bool:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return False
    return "MAX_TOKENS" in str(getattr(candidates[0], "finish_reason", "") or "").upper()


_SENTENCE_END_RE = re.compile(r"[.!?](?=\s|$)")


def trim_to_sentence(text: str) -> str:
    """Drop a trailing clipped fragment, keeping only whole sentences.

    A budget-capped generation can stop mid-sentence; shipping the fragment
    looks broken in the UI. Terminators are only recognized at a word
    boundary so decimals ("1.5 gallons") don't count. No terminator at all →
    returned unchanged (better a fragment than nothing).
    """
    matches = list(_SENTENCE_END_RE.finditer(text))
    if not matches:
        return text
    return text[: matches[-1].end()].rstrip()


def extract_text(response: Any) -> str:
    """Concatenated text parts of a Gemini response ('' when none)."""
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return ""
    parts = getattr(getattr(candidates[0], "content", None), "parts", None) or []
    chunks = [part.text for part in parts if getattr(part, "text", None)]
    return "\n".join(chunks)


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def parse_model_json(raw: str) -> Any:
    """Parse model JSON output, tolerating code fences and stray prose.

    Returns the parsed object/array, or None when nothing parseable exists.
    """
    if not raw:
        return None
    candidate = raw.strip()
    fence = _JSON_FENCE_RE.search(candidate)
    if fence:
        candidate = fence.group(1)
    try:
        return json.loads(candidate)
    except ValueError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = candidate.find(opener), candidate.rfind(closer)
        if 0 <= start < end:
            try:
                return json.loads(candidate[start : end + 1])
            except ValueError:
                continue
    return None


def generate_text(prompt: str, *, max_tokens: int = 300, model: str | None = None) -> str:
    """One-shot plain-text generation on the fast model."""
    _require_key()
    model = model or _fast_model()
    contents = [{"role": "user", "parts": [{"text": prompt}]}]
    response = _generate(model, contents, _utility_config(max_tokens, model, want_json=False))
    # Thinking depth varies per prompt, so the fixed headroom can still be
    # eaten mid-answer. One retry with double the budget beats shipping a
    # clipped sentence.
    if _hit_token_cap(response):
        response = _generate(
            model, contents, _utility_config(max_tokens * 2, model, want_json=False)
        )
    return extract_text(response)


def generate_json(prompt: str, *, max_tokens: int = 300, model: str | None = None) -> Any:
    """One-shot JSON generation. Returns parsed JSON or None."""
    _require_key()
    model = model or _fast_model()
    contents = [{"role": "user", "parts": [{"text": prompt}]}]
    response = _generate(model, contents, _utility_config(max_tokens, model, want_json=True))
    # A capped JSON answer is unparseable garbage — same double-budget retry.
    if _hit_token_cap(response):
        response = _generate(
            model, contents, _utility_config(max_tokens * 2, model, want_json=True)
        )
    return parse_model_json(extract_text(response))


def generate_vision_json(
    image_bytes: bytes,
    mime_type: str,
    prompt: str,
    *,
    max_tokens: int = 1500,
    model: str | None = None,
) -> Any:
    """Image + prompt → parsed JSON (or None). Vision keeps default thinking —
    detection quality matters more than a few hundred ms here."""
    _require_key()
    model = model or _vision_model()
    response = _client().models.generate_content(
        model=model,
        contents=[
            {
                "role": "user",
                "parts": [
                    {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode()}},
                    {"text": prompt},
                ],
            }
        ],
        config={"max_output_tokens": max_tokens, "response_mime_type": "application/json"},
    )
    return parse_model_json(extract_text(response))


def fetch_image_bytes(url: str, *, max_bytes: int = MAX_IMAGE_BYTES) -> tuple[bytes, str]:
    """Fetch an image URL for vision input, defensively.

    The previous provider fetched URLs on their side; Gemini takes inline
    bytes, so the fetch moved here — with guards, since the URL is
    user-supplied: http(s) only, must serve image/*, capped size, short
    timeout. Raises ValueError on any violation.
    """
    import requests

    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("Only http(s) image URLs are supported.")
    try:
        response = requests.get(url, timeout=10, stream=True, headers={"User-Agent": "ReNest-AI/1.0"})
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ValueError(f"Image could not be downloaded ({type(exc).__name__}).") from exc

    mime_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if not mime_type.startswith("image/"):
        raise ValueError("URL did not return an image.")

    data = b""
    for chunk in response.iter_content(chunk_size=64 * 1024):
        data += chunk
        if len(data) > max_bytes:
            raise ValueError("Image is too large (8 MB max).")
    if not data:
        raise ValueError("Image was empty.")
    return data, mime_type
