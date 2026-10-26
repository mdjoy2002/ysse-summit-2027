"""Payment integration application configuration."""

from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    """Configure payment transaction and provider adapter models."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.payments"
    label = "payments"
    verbose_name = "Payments"
