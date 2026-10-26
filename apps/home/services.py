"""Read-model helpers for homepage content."""

from django.urls import reverse
from django.utils import timezone

from .models import Announcement, FeaturedSpeaker, HomeHighlight, SummitEvent


def get_homepage_content():
    """Return the editable event configuration and currently visible content."""
    event = SummitEvent.objects.get(key=SummitEvent.Key.MAIN)
    current_time = timezone.now()
    registration_url = event.register_cta_url
    if registration_url == "#registration":
        registration_url = reverse("registrations:passes")

    return {
        "event": event,
        "registration_url": registration_url,
        "highlights": HomeHighlight.objects.filter(is_published=True),
        "featured_speakers": FeaturedSpeaker.objects.published()
        .filter(is_featured=True)
        .prefetch_related("categories")
        .order_by("display_order", "full_name")[:4],
        "announcements": Announcement.objects.filter(
            status=Announcement.Status.PUBLISHED,
            published_at__lte=current_time,
        )[:3],
    }
