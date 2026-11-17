from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import Campus, User
from hubs.models import DonationHub
from typing import Any
from listings.models import (
    Listing,
    ListingUpdate,
    RescueRequest,
    Reservation,
    ReservationMessage,
    RoomScanImage,
    RoomScanItemDraft,
    RoomScanSession,
    SavedListing,
    seed_default_move_out_tasks,
)
from listings.services import generate_handoff_pin


SEED_MEDIA = Path(__file__).resolve().parent / "seed_media"


def seed_photo(name: str) -> ContentFile:
    """Bundled real photo for demo content (see seed_media/)."""
    return ContentFile((SEED_MEDIA / f"{name}.jpg").read_bytes())


def needs_photo(field: Any) -> bool:
    """True when the field is empty or still holds a legacy SVG placeholder."""
    return not field or str(field.name).endswith(".svg")


def seed_reservation(listing: Listing, claimant: User, *, status: str, pickup_time_window: str) -> Reservation:
    """Put the seeded reservation on `listing` back into its seeded state.

    A demo user may have cancelled and re-reserved since the last run, so
    there can be several rows for the pair: the oldest is reused, and any
    other live claim is closed first because a listing allows only one.
    """
    reservation = Reservation.objects.filter(listing=listing, claimant=claimant).order_by("pk").first()
    if reservation is None:
        reservation = Reservation(listing=listing, claimant=claimant)
    Reservation.objects.filter(listing=listing, status__in=Reservation.ACTIVE_STATUSES).exclude(
        pk=reservation.pk
    ).update(status=Reservation.Status.CANCELLED, handoff_pin="")

    reservation.status = status
    reservation.pickup_time_window = pickup_time_window
    if status == Reservation.Status.CONFIRMED:
        # A confirmed handoff is completed with this PIN, so it must exist.
        reservation.handoff_pin = reservation.handoff_pin or generate_handoff_pin()
    else:
        reservation.handoff_pin = ""
    reservation.save()
    return reservation


class Command(BaseCommand):
    help = "Seeds two demo accounts with a campus, listings, a room scan, hubs and reservations."

    def handle(self, *args, **options):
        now = timezone.now()
        # The demo addresses cannot receive mail, so the accounts are created
        # verified (posting a listing requires it) and matched to a campus
        # (the leaderboard and other campus pages need one).
        campus, _ = Campus.objects.update_or_create(
            slug="pacific-state",
            defaults={
                "name": "Pacific State",
                "email_domains": "renest.local",
                "active": True,
            },
        )
        owner, _ = User.objects.update_or_create(
            email="maya@renest.local",
            defaults={
                "display_name": "Maya",
                "campus_name": campus.name,
                "campus": campus,
                "email_verified": True,
                "is_active": True,
            },
        )
        owner.set_password("demo-pass-123")
        owner.save()

        claimant, _ = User.objects.update_or_create(
            email="leo@renest.local",
            defaults={
                "display_name": "Leo",
                "campus_name": campus.name,
                "campus": campus,
                "email_verified": True,
                "is_active": True,
            },
        )
        claimant.set_password("demo-pass-123")
        claimant.save()

        DonationHub.objects.update_or_create(
            name="North Quad Move-Out Hub",
            defaults={
                "campus_name": "Pacific State",
                "zone_label": "North Quad",
                "description": "A staffed shelf for clean dorm essentials and unopened supplies.",
                "open_instructions": "Drop off boxed items between 4pm and 8pm during finals week.",
                "active": True,
                "is_demo": False,
            },
        )
        annex_hub, _ = DonationHub.objects.update_or_create(
            name="Library Annex Reuse Table",
            defaults={
                "campus_name": "Pacific State",
                "zone_label": "Library Annex",
                "description": "Quick-grab pickup point for small decor, lamps, and supplies.",
                "open_instructions": "Reserve in the app, then show your confirmation at pickup.",
                "active": True,
                "is_demo": False,
            },
        )
        DonationHub.objects.update_or_create(
            name="Campus Reuse Closet",
            defaults={
                "campus_name": "Pacific State",
                "zone_label": "Student Union East",
                "description": "A semester-round pickup spot for dorm organizers, lamps, and unopened supplies.",
                "open_instructions": "Reserve in the app, then bring your student ID to the Union East desk.",
                "active": True,
                "is_demo": False,
            },
        )
        DonationHub.objects.update_or_create(
            name="Finals Week Donation Dock",
            defaults={
                "campus_name": "Pacific State",
                "zone_label": "Maple Hall Service Entrance",
                "description": "A supervised drop zone for boxed move-out essentials during finals week.",
                "open_instructions": "Drop off clean items between 3pm and 8pm. Large furniture is not accepted.",
                "active": True,
                "is_demo": False,
            },
        )

        demo_session, _ = RoomScanSession.objects.update_or_create(
            owner=owner,
            name="North Hall Room Rescue Demo",
            defaults={
                "room_label": "North Hall 4B",
                "room_type": RoomScanSession.RoomType.DORM_ROOM,
                "pickup_zone": "North Hall lobby carts",
                "move_out_deadline": now + timedelta(days=2),
                "is_demo": False,
            },
        )

        image_one, _ = RoomScanImage.objects.update_or_create(
            scan_session=demo_session,
            position=1,
            defaults={},
        )
        if needs_photo(image_one.image):
            image_one.image.save("north-hall-demo-1.jpg", seed_photo("seed-room-a"), save=True)

        image_two, _ = RoomScanImage.objects.update_or_create(
            scan_session=demo_session,
            position=2,
            defaults={},
        )
        if needs_photo(image_two.image):
            image_two.image.save("north-hall-demo-2.jpg", seed_photo("seed-room-b"), save=True)

        listings: list[dict[str, Any]] = [
            {
                "title": "Stackable storage bins",
                "description": "Three clean cubes that fit under a twin XL bed.",
                "category": Listing.Category.STORAGE,
                "condition": Listing.Condition.GOOD,
                "price_type": Listing.PriceType.FREE,
                "price_amount": Decimal("0.00"),
                "estimated_retail_value": Decimal("22.00"),
                "pickup_zone": "Maple Hall lobby",
                "available_until": now + timedelta(hours=30),
                "status": Listing.Status.AVAILABLE,
                "photo": "seed-bins",
            },
            {
                "title": "Desk lamp with USB port",
                "description": "Warm light, no flicker, perfect for late-night study sessions.",
                "category": Listing.Category.LIGHTING,
                "condition": Listing.Condition.GOOD,
                "price_type": Listing.PriceType.LOW_COST,
                "price_amount": Decimal("8.00"),
                "estimated_retail_value": Decimal("26.00"),
                "pickup_zone": "Oak Hall mailroom",
                "available_until": now + timedelta(days=2),
                "status": Listing.Status.RESERVED,
                "photo": "seed-lamp-usb",
            },
            {
                "title": "Unopened toiletries bundle",
                "description": "Shampoo, body wash, and laundry pods that never got used.",
                "category": Listing.Category.TOILETRIES,
                "condition": Listing.Condition.NEW,
                "price_type": Listing.PriceType.FREE,
                "price_amount": Decimal("0.00"),
                "estimated_retail_value": Decimal("18.00"),
                "pickup_zone": "Student union lockers",
                "available_until": now + timedelta(days=1),
                "status": Listing.Status.AVAILABLE,
                "photo": "seed-toiletries",
            },
            {
                "title": "Velvet hanger pack",
                "description": "Forty hangers, barely used, rescued before move-out cleanup.",
                "category": Listing.Category.STORAGE,
                "condition": Listing.Condition.NEW,
                "price_type": Listing.PriceType.FREE,
                "price_amount": Decimal("0.00"),
                "estimated_retail_value": Decimal("16.00"),
                "pickup_zone": "North Quad hub",
                "available_until": now - timedelta(days=1),
                "status": Listing.Status.PICKED_UP,
                "photo": "seed-hangers",
            },
        ]

        created = []
        for item in listings:
            photo = item.pop("photo")
            listing, _ = Listing.objects.update_or_create(
                owner=owner,
                title=item["title"],
                defaults={
                    **item,
                    "source_scan_session": demo_session,
                    "is_demo": False,
                },
            )
            if needs_photo(listing.image):
                listing.image_thumb = None
                listing.image.save(
                    f"{item['title'].lower().replace(' ', '-')}.jpg",
                    seed_photo(photo),
                    save=True,
                )
            created.append(listing)

        # Leo's side of the marketplace, so each account has something to
        # reserve from the other.
        browse_listings = [
            ("Gooseneck desk lamp", "seed-lamp-gooseneck", Listing.Category.LIGHTING,
             Listing.Condition.GOOD, Listing.PriceType.LOW_COST, Decimal("6.00"), Decimal("19.00"), 3),
            ("Fabric storage cube", "seed-bin-cube", Listing.Category.STORAGE,
             Listing.Condition.GOOD, Listing.PriceType.FREE, Decimal("0.00"), Decimal("12.00"), 4),
            ("Shower caddy tote", "seed-shower-tote", Listing.Category.TOILETRIES,
             Listing.Condition.GOOD, Listing.PriceType.FREE, Decimal("0.00"), Decimal("14.00"), 5),
            ("Desk organizer caddy", "seed-desk-caddy", Listing.Category.SUPPLIES,
             Listing.Condition.NEW, Listing.PriceType.LOW_COST, Decimal("5.00"), Decimal("15.00"), 4),
            ("Clip-on mini fan", "seed-mini-fan", Listing.Category.COMFORT,
             Listing.Condition.GOOD, Listing.PriceType.LOW_COST, Decimal("7.00"), Decimal("21.00"), 6),
            ("Moving boxes and packing material", "seed-packing-scraps", Listing.Category.OTHER,
             Listing.Condition.FAIR, Listing.PriceType.FREE, Decimal("0.00"), Decimal("8.00"), 2),
        ]
        for title, photo, category, condition, price_type, price, retail, days in browse_listings:
            listing, _ = Listing.objects.update_or_create(
                owner=claimant,
                title=title,
                defaults={
                    "description": f"{title} in honest condition, ready for pickup before move-out ends.",
                    "category": category,
                    "condition": condition,
                    "price_type": price_type,
                    "price_amount": price,
                    "estimated_retail_value": retail,
                    "pickup_zone": "Union East front desk",
                    "available_until": now + timedelta(days=days),
                    "status": Listing.Status.AVAILABLE,
                    "is_demo": False,
                },
            )
            if needs_photo(listing.image):
                listing.image_thumb = None
                listing.image.save(f"{photo}.jpg", seed_photo(photo), save=True)

        reserved_listing = next(item for item in created if item.status == Listing.Status.RESERVED)
        confirmed_handoff = seed_reservation(
            reserved_listing,
            claimant,
            status=Reservation.Status.CONFIRMED,
            pickup_time_window="Tomorrow between 5pm and 6pm",
        )
        for sender, body in (
            (claimant, "Hi Maya! Is the lamp still good for tomorrow?"),
            (owner, "Yes. I will be at the Oak Hall mailroom at 5:15pm."),
        ):
            ReservationMessage.objects.get_or_create(reservation=confirmed_handoff, sender=sender, body=body)

        picked_up_listing = next(item for item in created if item.status == Listing.Status.PICKED_UP)
        seed_reservation(
            picked_up_listing,
            claimant,
            status=Reservation.Status.COMPLETED,
            pickup_time_window="Completed pickup yesterday",
        )

        RescueRequest.objects.update_or_create(
            seeker=claimant,
            title="Mini fridge for next semester",
            defaults={
                "description": "Moving into a single in August and it comes without a fridge.",
                "category": Listing.Category.OTHER,
                "pickup_zone": "Union East front desk",
                "needed_by": now + timedelta(days=5),
                "budget_amount": Decimal("40.00"),
                "urgency": RescueRequest.Urgency.SOON,
            },
        )

        ListingUpdate.objects.update_or_create(
            listing=reserved_listing,
            author=claimant,
            body="I can meet near the Oak Hall mailroom after my chemistry lab.",
        )
        ListingUpdate.objects.update_or_create(
            listing=reserved_listing,
            author=owner,
            body="Perfect. I will bring the lamp down around 5:15pm.",
        )
        SavedListing.objects.get_or_create(user=claimant, listing=created[0])

        draft_definitions = [
            {
                "title": "Desk lamp #1",
                "source_image": image_one,
                "hotspot_box": {"x": 0.66, "y": 0.14, "width": 0.18, "height": 0.24},
                "category": Listing.Category.LIGHTING,
                "condition": Listing.Condition.GOOD,
                "price_type": Listing.PriceType.LOW_COST,
                "price_amount": Decimal("8.00"),
                "estimated_retail_value": Decimal("26.00"),
                "triage_status": RoomScanItemDraft.TriageStatus.DONE,
                "notes": "Tagged from the desk corner. Good candidate for a fast paid pickup.",
                "linked_listing": reserved_listing,
            },
            {
                "title": "Storage bin #2",
                "source_image": image_one,
                "hotspot_box": {"x": 0.08, "y": 0.08, "width": 0.24, "height": 0.22},
                "category": Listing.Category.STORAGE,
                "condition": Listing.Condition.GOOD,
                "price_type": Listing.PriceType.FREE,
                "price_amount": Decimal("0.00"),
                "estimated_retail_value": Decimal("22.00"),
                "triage_status": RoomScanItemDraft.TriageStatus.DONE,
                "notes": "Marked for a free post because it is clean and easy to carry.",
                "linked_listing": created[0],
            },
            {
                "title": "Bathroom tote #3",
                "source_image": image_two,
                "hotspot_box": {"x": 0.12, "y": 0.42, "width": 0.19, "height": 0.18},
                "category": Listing.Category.TOILETRIES,
                "condition": Listing.Condition.NEW,
                "price_type": Listing.PriceType.FREE,
                "price_amount": Decimal("0.00"),
                "estimated_retail_value": Decimal("18.00"),
                "triage_status": RoomScanItemDraft.TriageStatus.DONE,
                "notes": "Sealed toiletries routed to a high-trust donation handoff.",
                "linked_listing": created[2],
                "donation_hub": annex_hub,
            },
            {
                "title": "Desk supplies caddy #4",
                "source_image": image_two,
                "hotspot_box": {"x": 0.38, "y": 0.44, "width": 0.22, "height": 0.16},
                "category": Listing.Category.SUPPLIES,
                "condition": Listing.Condition.GOOD,
                "price_type": Listing.PriceType.FREE,
                "price_amount": Decimal("0.00"),
                "estimated_retail_value": Decimal("14.00"),
                "triage_status": RoomScanItemDraft.TriageStatus.DONATE,
                "notes": "Still pending donation routing in the board view.",
            },
            {
                "title": "Mini fan #5",
                "source_image": image_one,
                "hotspot_box": {"x": 0.36, "y": 0.48, "width": 0.24, "height": 0.17},
                "category": Listing.Category.COMFORT,
                "condition": Listing.Condition.GOOD,
                "price_type": Listing.PriceType.LOW_COST,
                "price_amount": Decimal("10.00"),
                "estimated_retail_value": Decimal("28.00"),
                "triage_status": RoomScanItemDraft.TriageStatus.SELL,
                "notes": "Suggested for a quick low-cost post before the room clears out.",
            },
            {
                "title": "Old packing scraps #6",
                "source_image": image_two,
                "hotspot_box": {"x": 0.72, "y": 0.58, "width": 0.12, "height": 0.1},
                "category": Listing.Category.OTHER,
                "condition": Listing.Condition.FAIR,
                "price_type": Listing.PriceType.FREE,
                "price_amount": Decimal("0.00"),
                "estimated_retail_value": Decimal("0.00"),
                "triage_status": RoomScanItemDraft.TriageStatus.TOSS,
                "notes": "Not worth rescuing. Useful to show honest triage in the board.",
            },
        ]

        for definition in draft_definitions:
            RoomScanItemDraft.objects.update_or_create(
                scan_session=demo_session,
                title=definition["title"],
                defaults=definition,
            )

        # The API creates the move-out checklist when a scan is started; a
        # session created here needs the same so its plan page is not empty.
        seed_default_move_out_tasks(demo_session)
        demo_session.refresh_progress(commit=True)

        self.stdout.write(self.style.SUCCESS("ReNest demo data seeded."))
