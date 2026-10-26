"""Validators for homepage-managed uploads and local call-to-action links."""

from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.utils.translation import gettext_lazy as _


MAX_IMAGE_SIZE = 5 * 1024 * 1024


def validate_image_size(image):
    """Reject oversized homepage image uploads before they are saved."""
    if image.size > MAX_IMAGE_SIZE:
        raise ValidationError(_("Image files must be 5 MB or smaller."))


def validate_video_url(value):
    """Only allow HTTPS URLs for externally hosted hero videos."""
    URLValidator(schemes=["https"])(value)


def validate_local_destination(value):
    """Keep event calls to action on this site or on a homepage section."""
    if value.startswith("#"):
        return

    parsed = urlsplit(value)
    if not value.startswith("/") or value.startswith("//") or parsed.netloc:
        raise ValidationError(
            _("Use a site path beginning with / or a homepage anchor beginning with #.")
        )
