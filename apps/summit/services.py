"""Read-model helpers for the summit details page."""

from django.urls import reverse

from apps.home.models import SummitEvent


def get_summit_page_content():
    """Return the central event configuration and a resolved registration link."""
    event = SummitEvent.objects.get(key=SummitEvent.Key.MAIN)
    registration_url = event.register_cta_url
    if registration_url == "#registration":
        registration_url = reverse("registrations:passes")
    elif registration_url.startswith("#"):
        registration_url = f"{reverse('home:index')}{registration_url}"

    return {
        "event": event,
        "registration_url": registration_url,
    }
