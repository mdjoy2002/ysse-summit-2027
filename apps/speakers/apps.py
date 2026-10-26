"""Application configuration for speaker and guest profiles."""

from django.apps import AppConfig


class SpeakersConfig(AppConfig):
    """Register editable speaker categories and public profile pages."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.speakers"
    label = "speakers"
    verbose_name = "Speakers and VVIP Guests"
