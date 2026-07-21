"""Generate PDF donation receipts for listings marked as donated to a hub."""
from __future__ import annotations

import io
import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from django.core.files.base import ContentFile
from django.utils import timezone

if TYPE_CHECKING:
    from reportlab.pdfgen.canvas import Canvas

    from .models import Listing

logger = logging.getLogger(__name__)

_BRAND_COLOR = (0.541, 0.114, 0.271)  # #8a1d45 as (r, g, b) floats
_MUTED_COLOR = (0.42, 0.39, 0.34)
_INK_COLOR = (0.11, 0.10, 0.086)


def generate_donation_receipt(listing: Listing) -> ContentFile | None:
    """
    Build a PDF donation receipt and return it as a Django ContentFile.
    Returns None if reportlab is unavailable or generation fails.
    The caller is responsible for saving it to listing.donation_receipt.
    """
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.units import inch
        from reportlab.pdfgen import canvas as rl_canvas
    except ImportError:
        logger.warning("reportlab is not installed — donation receipt skipped")
        return None

    try:
        buf = io.BytesIO()
        width, height = LETTER
        c = rl_canvas.Canvas(buf, pagesize=LETTER)

        _draw_receipt(c, listing, width, height)
        c.save()

        filename = f"receipt-{listing.pk}-{timezone.now().strftime('%Y%m%d')}.pdf"
        return ContentFile(buf.getvalue(), name=filename)
    except Exception:
        logger.exception("Failed to generate donation receipt for listing %s", listing.pk)
        return None


def _draw_receipt(c: Canvas, listing: Listing, width: float, height: float) -> None:
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas as rl_canvas

    margin = inch
    y = height - margin

    # ── Header bar ────────────────────────────────────────────────────────────
    r, g, b = _BRAND_COLOR
    c.setFillColorRGB(r, g, b)
    c.rect(0, height - 1.1 * inch, width, 1.1 * inch, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(margin, height - 0.72 * inch, "ReNest")
    c.setFont("Helvetica", 11)
    c.drawRightString(width - margin, height - 0.72 * inch, "Donation Receipt")

    y = height - 1.5 * inch

    # ── Title ─────────────────────────────────────────────────────────────────
    _set_ink(c)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(margin, y, "Donation Acknowledgment")
    y -= 0.35 * inch

    # ── Date + Receipt No ────────────────────────────────────────────────────
    _set_muted(c)
    c.setFont("Helvetica", 10)
    issued = timezone.now().strftime("%B %d, %Y")
    c.drawString(margin, y, f"Date Issued: {issued}")
    c.drawRightString(width - margin, y, f"Receipt #: DC-{listing.pk:06d}")
    y -= 0.55 * inch

    # ── Donor block ───────────────────────────────────────────────────────────
    _set_ink(c)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margin, y, "Donor Information")
    y -= 0.25 * inch
    _set_muted(c)
    c.setFont("Helvetica", 10)
    owner = listing.owner
    donor_name = owner.get_full_name() or owner.email
    c.drawString(margin, y, f"Name:   {donor_name}")
    y -= 0.22 * inch
    c.drawString(margin, y, f"Email:  {owner.email}")
    y -= 0.45 * inch

    # ── Hub block ─────────────────────────────────────────────────────────────
    _set_ink(c)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margin, y, "Recipient")
    y -= 0.25 * inch
    _set_muted(c)
    c.setFont("Helvetica", 10)
    if listing.donation_hub:
        hub = listing.donation_hub
        c.drawString(margin, y, f"Hub:    {hub.name}")
        y -= 0.22 * inch
        c.drawString(margin, y, f"Zone:   {hub.zone_label}")
        y -= 0.22 * inch
        c.drawString(margin, y, f"Campus: {hub.campus_name}")
    else:
        c.drawString(margin, y, "Campus Donation Hub")
    y -= 0.45 * inch

    # ── Item table ────────────────────────────────────────────────────────────
    _set_ink(c)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margin, y, "Donated Item")
    y -= 0.3 * inch

    # Table header
    r, g, b = _BRAND_COLOR
    c.setFillColorRGB(r, g, b)
    c.rect(margin, y - 0.05 * inch, width - 2 * margin, 0.3 * inch, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(margin + 0.1 * inch, y + 0.08 * inch, "Description")
    c.drawRightString(width - margin - 0.1 * inch, y + 0.08 * inch, "Est. Fair Market Value")
    y -= 0.05 * inch

    # Table row
    c.setFillColorRGB(0.97, 0.96, 0.94)
    c.rect(margin, y - 0.28 * inch, width - 2 * margin, 0.28 * inch, fill=1, stroke=0)
    _set_ink(c)
    c.setFont("Helvetica", 9)
    item_label = listing.title[:80]
    c.drawString(margin + 0.1 * inch, y - 0.18 * inch, item_label)
    retail = Decimal(listing.estimated_retail_value or 0)
    value_label = f"${retail:.2f}" if retail > 0 else "Not specified"
    c.drawRightString(width - margin - 0.1 * inch, y - 0.18 * inch, value_label)
    y -= 0.28 * inch

    # Total row
    _set_ink(c)
    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(width - margin, y - 0.18 * inch, f"Total: {value_label}")
    y -= 0.55 * inch

    # ── Legal disclaimer ──────────────────────────────────────────────────────
    _set_muted(c)
    c.setFont("Helvetica-Oblique", 8)
    disclaimer = (
        "ReNest facilitates peer-to-peer item rescue on college campuses. "
        "This document acknowledges the donation of the above item(s) and may be used "
        "for personal tax records. ReNest is not a registered 501(c)(3) organization. "
        "Consult a tax professional regarding deductibility."
    )
    _draw_wrapped(c, disclaimer, margin, y, width - 2 * margin, 12)
    y -= 0.6 * inch

    # ── Footer line ───────────────────────────────────────────────────────────
    r, g, b = _BRAND_COLOR
    c.setStrokeColorRGB(r, g, b)
    c.setLineWidth(0.5)
    c.line(margin, 0.75 * inch, width - margin, 0.75 * inch)
    _set_muted(c)
    c.setFont("Helvetica", 8)
    c.drawCentredString(width / 2, 0.55 * inch, "renest.app  ·  Campus Move-Out Rescue Marketplace")


def _set_ink(c: Canvas) -> None:
    r, g, b = _INK_COLOR
    c.setFillColorRGB(r, g, b)


def _set_muted(c: Canvas) -> None:
    r, g, b = _MUTED_COLOR
    c.setFillColorRGB(r, g, b)


def _draw_wrapped(c: Canvas, text: str, x: float, y: float, max_width: float, line_height: int) -> None:
    from reportlab.lib.units import inch

    words = text.split()
    line: list[str] = []
    for word in words:
        test = " ".join(line + [word])
        if c.stringWidth(test, "Helvetica-Oblique", 8) <= max_width:
            line.append(word)
        else:
            c.drawString(x, y, " ".join(line))
            y -= line_height / 72 * inch
            line = [word]
    if line:
        c.drawString(x, y, " ".join(line))
