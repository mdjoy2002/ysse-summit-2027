"""Local development settings."""

from .base import *
from .base import env


DEBUG = env.bool("DEBUG", default=True)
SECRET_KEY = SECRET_KEY or "django-insecure-local-development-only"
ALLOWED_HOSTS = list(
    dict.fromkeys(
        get_allowed_hosts() or ["localhost", "127.0.0.1", "[::1]"]
    )
)
PAYMENT_PROVIDER_CLASS = env(
    "PAYMENT_PROVIDER_CLASS",
    default="apps.payments.providers.demo.DemoProvider",
)
PAYMENT_DEMO_ENABLED = env.bool("PAYMENT_DEMO_ENABLED", default=True)
