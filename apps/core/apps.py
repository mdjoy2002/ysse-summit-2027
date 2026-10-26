"""Application configuration for shared project functionality."""

from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Register functionality shared across the summit website."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"
