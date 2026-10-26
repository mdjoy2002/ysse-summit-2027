"""Application configuration for YSSE history and impact content."""

from django.apps import AppConfig


class AboutConfig(AppConfig):
    """Register editable About YSSE content."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.about"
    label = "about"
    verbose_name = "About YSSE"
