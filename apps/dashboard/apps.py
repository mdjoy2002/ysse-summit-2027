"""Application configuration for the private staff management dashboard."""

from django.apps import AppConfig


class DashboardConfig(AppConfig):
    """Register the management dashboard without adding domain data models."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.dashboard"
    label = "dashboard"
    verbose_name = "Management dashboard"
