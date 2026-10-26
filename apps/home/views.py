"""HTTP views for the homepage."""

from django.shortcuts import render

from .services import get_homepage_content


def homepage(request):
    """Render the public homepage with only published, approved content."""
    return render(request, "home/index.html", get_homepage_content())
