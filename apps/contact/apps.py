"""Public contact, policy, news, and newsletter application."""

from django.apps import AppConfig


class ContactConfig(AppConfig):
    """Register public communication and information models."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.contact"
    label = "contact"
    verbose_name = "Contact and public information"
