"""Registration and ticketing application configuration."""

from django.apps import AppConfig


class RegistrationsConfig(AppConfig):
    """Configure summit registration and ticketing features."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.registrations"
    label = "registrations"
    verbose_name = "Registration and Ticketing"
