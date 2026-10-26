"""HTTP views for the summit details page."""

from django.shortcuts import render

from .services import get_summit_page_content


def summit_details(request):
    """Render the summit page from the shared, admin-managed event record."""
    return render(request, "summit/details.html", get_summit_page_content())
