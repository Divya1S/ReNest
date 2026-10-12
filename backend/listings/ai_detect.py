import logging
from pathlib import Path
from typing import Any

from .ai_client import generate_vision_json

logger = logging.getLogger(__name__)

# The prompt asks for 2 to 8 items. Only the upper bound can be enforced on
# the answer: a photo of a nearly empty room honestly has fewer than two, and
# rejecting that would turn a correct result into an error.
MAX_DETECTED_ITEMS = 8

_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

_PROMPT = (
    "Analyze this dorm or apartment room photo. Identify 2–8 distinct items "
    "a student could realistically sell or donate during move-out.\n\n"
    "For every item return:\n"
    "- title: short descriptive name (2–5 words)\n"
    "- category: exactly one of: storage | lighting | supplies | comfort | toiletries | decor | other\n"
    "- condition: exactly one of: new | good | fair\n"
    "- price_type: \"free\" for low-value items, \"low_cost\" for items worth $5–$40\n"
    "- estimated_retail_value: approximate original retail price as a number (USD)\n"
    "- hotspot_box: {\"x\": float, \"y\": float, \"width\": float, \"height\": float} "
    "where all values are 0.0–1.0 representing normalized coordinates of the item "
    "in the image (x=left edge, y=top edge)\n\n"
    "Focus on: lamps, fans, storage bins, mirrors, hangers, school supplies, "
    "toiletries, small decor, mini appliances.\n"
    "Skip: walls, floors, major furniture, people, built-in fixtures.\n\n"
    "Return ONLY a valid JSON array, no markdown, no explanation."
)


def detect_items_in_image(image_file: Any) -> Any:
    """
    Call Gemini vision to detect rescue-worthy items in a room photo.

    Accepts a filesystem path or a Django File/FieldFile; file objects are
    read through the storage API, so this works identically on local disk and
    S3/R2 remote storage (remote FieldFiles have no usable .path).

    Returns a list of dicts: {title, category, condition, price_type,
    estimated_retail_value, hotspot_box: {x, y, width, height}}.
    Raises RuntimeError if the API key is missing or the call fails.
    """
    if isinstance(image_file, (str, Path)):
        with open(image_file, "rb") as fh:
            image_bytes = fh.read()
        name = str(image_file)
    else:
        image_file.open("rb")
        try:
            image_bytes = image_file.read()
        finally:
            image_file.close()
        name = getattr(image_file, "name", "") or ""
    media_type = _MEDIA_TYPES.get(Path(name).suffix.lower(), "image/jpeg")

    items = generate_vision_json(image_bytes, media_type, _PROMPT, max_tokens=1500)
    if not isinstance(items, list):
        raise ValueError("Model returned unexpected shape: expected a JSON array.")
    return items


def clamp(value: Any, lo: Any = 0.0, hi: Any = 1.0, default: float = 0.0) -> float:
    """Coerce a model-supplied number into range, tolerating junk.

    The model occasionally emits null, a string or a missing key; a TypeError
    here used to abort the whole detection run and discard every other item it
    found in the same photo.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number:  # NaN
        number = default
    return max(lo, min(hi, number))


def box_overlap(a: Any, b: Any) -> float:
    """Intersection over union of two hotspot boxes (0.0 when either is unusable).

    Used to recognise that a freshly detected item is the same object as a
    draft that already exists for the photo.
    """
    if not isinstance(a, dict) or not isinstance(b, dict):
        return 0.0
    ax, ay = clamp(a.get("x")), clamp(a.get("y"))
    aw, ah = clamp(a.get("width")), clamp(a.get("height"))
    bx, by = clamp(b.get("x")), clamp(b.get("y"))
    bw, bh = clamp(b.get("width")), clamp(b.get("height"))
    overlap_w = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    overlap_h = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    intersection = overlap_w * overlap_h
    union = aw * ah + bw * bh - intersection
    return intersection / union if union > 0 else 0.0


def sanitise_suggestions(suggestions: Any, *, limit: int = MAX_DETECTED_ITEMS) -> Any:
    """Validate and clamp the model's output before writing to DB.

    At most `limit` items are kept: the "up to 8" in the prompt is a request,
    and a model that ignores it must not be able to flood a session with
    drafts.
    """
    valid_categories = {"storage", "lighting", "supplies", "comfort", "toiletries", "decor", "other"}
    valid_conditions = {"new", "good", "fair"}
    valid_price_types = {"free", "low_cost"}

    if not isinstance(suggestions, list):
        return []

    cleaned: list[dict[str, Any]] = []
    for item in suggestions:
        if len(cleaned) >= limit:
            break
        if not isinstance(item, dict):
            continue
        try:
            box_raw = item.get("hotspot_box")
            if not isinstance(box_raw, dict):
                box_raw = {}
            w = clamp(box_raw.get("width"), 0.05, 1.0, default=0.2)
            h = clamp(box_raw.get("height"), 0.05, 1.0, default=0.2)
            # Make sure box fits inside image
            x = clamp(box_raw.get("x"), 0.0, 1.0 - w, default=0.1)
            y = clamp(box_raw.get("y"), 0.0, 1.0 - h, default=0.1)

            price_type = item.get("price_type", "free")
            if price_type not in valid_price_types:
                price_type = "free"

            retail = clamp(item.get("estimated_retail_value"), 0.0, 300.0, default=0.0)

            title = str(item.get("title") or "Room item").strip()[:140] or "Room item"
            cleaned.append({
                "title": title,
                "category": item.get("category", "other") if item.get("category") in valid_categories else "other",
                "condition": item.get("condition", "good") if item.get("condition") in valid_conditions else "good",
                "price_type": price_type,
                "estimated_retail_value": retail,
                "hotspot_box": {"x": x, "y": y, "width": w, "height": h},
            })
        except Exception:
            # One malformed suggestion must not lose the rest of the photo.
            logger.warning("Skipping malformed AI suggestion: %r", item, exc_info=True)
            continue
    return cleaned
