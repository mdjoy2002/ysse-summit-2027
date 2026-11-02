"""Validation for participant-submitted mobile payment evidence."""

from pathlib import Path

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.home.validators import validate_image_size


ALLOWED_PAYMENT_PROOF_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def validate_payment_proof(image):
    """Allow only small raster images accepted by Django's ImageField."""
    validate_image_size(image)
    if Path(image.name).suffix.lower() not in ALLOWED_PAYMENT_PROOF_EXTENSIONS:
        raise ValidationError(
            _("Payment proof must be a JPG, PNG, or WebP image.")
        )
