"""Application configuration for the summit agenda."""

from django.apps import AppConfig


class ScheduleConfig(AppConfig):
    """Register editable event sessions and the public agenda."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.schedule"
    label = "schedule"
    verbose_name = "Event Schedule"
