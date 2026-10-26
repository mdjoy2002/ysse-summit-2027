from django.apps import AppConfig


class SummitConfig(AppConfig):
    """Presentation app for the summit details page."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.summit"
    label = "summit"
