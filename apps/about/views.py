"""HTTP views for the About YSSE page."""

from django.shortcuts import render

from .services import get_about_page_content


def about_page(request):
    """Render YSSE's editable history and only verified, published content."""
    return render(request, "about/index.html", get_about_page_content())
