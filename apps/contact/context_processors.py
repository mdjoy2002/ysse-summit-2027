"""Shared public contact and social links for the global footer."""

from .models import PublicInformationSettings


def public_information(request):
    """Expose configured contact/social data and the official email site-wide."""
    settings = PublicInformationSettings.objects.filter(
        key=PublicInformationSettings.Key.MAIN
    ).first()
    return {"public_information": settings}
