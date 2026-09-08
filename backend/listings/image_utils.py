from __future__ import annotations

import io

from django.core.files.uploadedfile import InMemoryUploadedFile, UploadedFile
from PIL import Image, ImageOps
from rest_framework.exceptions import ValidationError

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_DIMENSION = 1920
JPEG_QUALITY = 85
# Maximum pixels in either dimension before we reject (prevents zip-bomb pixel floods)
MAX_PIXEL_DIMENSION = 8000

# Second line of defence: Pillow itself refuses to decode anything larger, so a
# decode reached through another code path cannot exhaust memory either.
Image.MAX_IMAGE_PIXELS = MAX_PIXEL_DIMENSION * MAX_PIXEL_DIMENSION

# Known magic bytes for allowed image types. Tuples of (offset, expected_bytes).
_MAGIC: list[tuple[int, bytes]] = [
    (0, b"\xff\xd8\xff"),              # JPEG
    (0, b"\x89PNG\r\n\x1a\n"),        # PNG
    (0, b"GIF87a"),                    # GIF87
    (0, b"GIF89a"),                    # GIF89
    (8, b"WEBP"),                      # WebP (bytes 8-11 of a RIFF container)
]


def _check_magic_bytes(upload: UploadedFile, field_name: str) -> None:
    """Reject files whose leading bytes don't match a known image format."""
    upload.seek(0)
    header = upload.read(16)
    upload.seek(0)
    for offset, magic in _MAGIC:
        if header[offset: offset + len(magic)] == magic:
            return
    raise ValidationError({field_name: "File does not appear to be a valid image."})


def compress_image(upload: UploadedFile, field_name: str = "image") -> InMemoryUploadedFile:
    """
    Validate size + magic bytes, decompress, resize to fit within MAX_DIMENSION,
    and re-encode as JPEG at JPEG_QUALITY.  Returns an InMemoryUploadedFile
    suitable for saving to an ImageField.
    """
    if upload.size and upload.size > MAX_UPLOAD_BYTES:
        raise ValidationError(
            {field_name: f"Image must be under 10 MB (received {upload.size // (1024 * 1024)} MB)."}
        )

    _check_magic_bytes(upload, field_name)

    try:
        img: Image.Image = Image.open(upload)
    except Exception:
        raise ValidationError({field_name: "Upload a valid image file."})

    # Check the dimensions from the header BEFORE decoding. Image.open() is
    # lazy, so this rejects a decompression bomb (a small file that expands to
    # hundreds of megabytes of pixels) without ever allocating that memory.
    if max(img.width, img.height) > MAX_PIXEL_DIMENSION:
        raise ValidationError(
            {field_name: f"Image dimensions must not exceed {MAX_PIXEL_DIMENSION}px on any side."}
        )

    try:
        # Phone cameras record orientation in EXIF rather than rotating pixels;
        # without this, portrait photos are stored (and shown) sideways.
        img = ImageOps.exif_transpose(img) or img
        img.load()
    except Exception:
        raise ValidationError({field_name: "Upload a valid image file."})

    if img.mode != "RGB":
        img = img.convert("RGB")

    if max(img.width, img.height) > MAX_DIMENSION:
        img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    buf.seek(0)

    stem = (upload.name or "image").rsplit(".", 1)[0]
    return InMemoryUploadedFile(
        file=buf,
        field_name=field_name,
        name=f"{stem}.jpg",
        content_type="image/jpeg",
        size=buf.getbuffer().nbytes,
        charset=None,
    )


THUMB_DIMENSION = 480
THUMB_QUALITY = 78


def make_thumbnail(stored_file: object, max_dim: int = THUMB_DIMENSION) -> "io.BytesIO | None":
    """
    Build a small JPEG rendition from an already-stored image FieldFile.
    Returns a BytesIO ready for ContentFile, or None when the source can't
    be rasterised (e.g. seeded SVG placeholders).
    """
    try:
        stored_file.open("rb")  # type: ignore[attr-defined]
        img: Image.Image = Image.open(stored_file)  # type: ignore[arg-type]
        img.load()
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=THUMB_QUALITY, optimize=True)
        buf.seek(0)
        return buf
    except Exception:
        return None
