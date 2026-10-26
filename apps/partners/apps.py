"""Application configuration for sponsors and partnership inquiries."""

from django.apps import AppConfig


class PartnersConfig(AppConfig):
    """Register sponsor directory and partnership contact workflows."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.partners"
    label = "partners"
    verbose_name = "Partners and Sponsors"
