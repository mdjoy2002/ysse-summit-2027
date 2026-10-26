"""Application configuration for homepage content and presentation."""

from django.apps import AppConfig


class HomeConfig(AppConfig):
    """Register homepage event, highlight, speaker, and news content."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.home"
    label = "home"
    verbose_name = "Homepage"
