"""Bring existing rows in line with the constraints added in 0042.

Data only, on purpose. PostgreSQL refuses to ALTER a table that still has
pending trigger events from row changes made earlier in the same
transaction, so the clean-up and the constraints live in separate migrations.
"""

import secrets

from django.db import migrations
from django.db.models import Count, F
from django.utils import timezone


def clamp_negative_listing_money(apps, schema_editor):
    Listing = apps.get_model("listings", "Listing")
    Listing.objects.filter(price_amount__lt=0).update(price_amount=0)
    Listing.objects.filter(estimated_retail_value__lt=0).update(estimated_retail_value=0)


def clamp_feedback_ratings(apps, schema_editor):
    HandoffFeedback = apps.get_model("listings", "HandoffFeedback")
    HandoffFeedback.objects.filter(rating__lt=1).update(rating=1)
    HandoffFeedback.objects.filter(rating__gt=5).update(rating=5)


def merge_duplicate_disputes(apps, schema_editor):
    """Keep one dispute per (reservation, opener) and undo the extra strikes.

    Duplicates came from a racing get_or_create. Each one also struck the
    other party's trust score, so every removed row gives one strike back.
    An open dispute is preferred as the survivor so staff still see it.
    """
    Dispute = apps.get_model("listings", "Dispute")
    Reservation = apps.get_model("listings", "Reservation")
    user_app, user_model = Reservation._meta.get_field("claimant").remote_field.model._meta.label.split(".")
    User = apps.get_model(user_app, user_model)
    can_revert_strikes = any(field.name == "trust_strikes" for field in User._meta.get_fields())

    duplicated = (
        Dispute.objects.values("reservation_id", "opened_by_id")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
    )
    for group in duplicated:
        rows = list(
            Dispute.objects.filter(
                reservation_id=group["reservation_id"], opened_by_id=group["opened_by_id"]
            )
            .select_related("reservation__listing")
            .order_by("created_at", "id")
        )
        keeper = next((row for row in rows if row.status == "open"), rows[0])
        extras = [row for row in rows if row.pk != keeper.pk]
        if can_revert_strikes:
            reservation = keeper.reservation
            other_party_id = (
                reservation.listing.owner_id
                if keeper.opened_by_id == reservation.claimant_id
                else reservation.claimant_id
            )
            for _ in extras:
                User.objects.filter(pk=other_party_id, trust_strikes__gt=0).update(
                    trust_strikes=F("trust_strikes") - 1
                )
        Dispute.objects.filter(pk__in=[row.pk for row in extras]).delete()


def release_stale_and_duplicate_holds(apps, schema_editor):
    """Leave at most one ACTIVE hold per listing.

    Holds past expires_at have already auto-released in every reader's eyes;
    record that. Of any remaining duplicates the oldest hold is the one that
    should have won the check-then-insert race, so it stays.
    """
    StorageHold = apps.get_model("listings", "StorageHold")
    now = timezone.now()
    StorageHold.objects.filter(status="active", expires_at__lte=now).update(
        status="released", updated_at=now
    )
    duplicated = (
        StorageHold.objects.filter(status="active")
        .values("listing_id")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
    )
    for group in duplicated:
        hold_ids = list(
            StorageHold.objects.filter(listing_id=group["listing_id"], status="active")
            .order_by("created_at", "id")
            .values_list("id", flat=True)
        )
        StorageHold.objects.filter(pk__in=hold_ids[1:]).update(status="released", updated_at=now)


def align_handoff_pins(apps, schema_editor):
    """A handoff can now only be completed with its PIN.

    Confirmed rows from before PINs existed (or from the old batch confirm)
    would be impossible to complete, so they get one. Closed rows no longer
    need the secret and drop it.
    """
    Reservation = apps.get_model("listings", "Reservation")
    pending = Reservation.objects.filter(
        status__in=("confirmed", "expired_unresolved"), handoff_pin=""
    )
    for reservation in pending.iterator(chunk_size=200):
        reservation.handoff_pin = str(secrets.randbelow(1_000_000)).zfill(6)
        reservation.save(update_fields=["handoff_pin"])
    Reservation.objects.filter(status__in=("completed", "cancelled")).exclude(handoff_pin="").update(
        handoff_pin=""
    )


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0040_listingboostpayment"),
    ]

    operations = [
        migrations.RunPython(clamp_negative_listing_money, migrations.RunPython.noop),
        migrations.RunPython(clamp_feedback_ratings, migrations.RunPython.noop),
        migrations.RunPython(merge_duplicate_disputes, migrations.RunPython.noop),
        migrations.RunPython(release_stale_and_duplicate_holds, migrations.RunPython.noop),
        migrations.RunPython(align_handoff_pins, migrations.RunPython.noop),
    ]
