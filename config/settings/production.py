"""Production settings with secure defaults."""

from django.core.exceptions import ImproperlyConfigured

from .base import *
from .base import env


DEBUG = False
PAYMENT_DEMO_ENABLED = False

if len(SECRET_KEY) < 50 or SECRET_KEY == "django-insecure-local-development-only":
    raise ImproperlyConfigured(
        "Set a SECRET_KEY of at least 50 characters in the production environment."
    )

if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("Set ALLOWED_HOSTS in the production environment.")

SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31_536_000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(
    "SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False
)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
