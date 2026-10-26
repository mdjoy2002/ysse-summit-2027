"""Validation helpers for public information module uploads and links."""

from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.core.files.storage import FileSystemStorage
from django.utils.translation import gettext_lazy as _

from apps.home.validators import validate_image_size


MAX_PRESS_ATTACHMENT_SIZE = 10 * 1024 * 1024
SOCIAL_URL_VALIDATOR = URLValidator(schemes=["https"])


def private_press_storage():
    """Keep press attachments outside the public media/static web roots."""
    return FileSystemStorage(location=settings.PRIVATE_MEDIA_ROOT)


def validate_optional_https_url(value):
    """Require configured external links to use HTTPS."""
    if value:
        SOCIAL_URL_VALIDATOR(value)
        parsed = urlsplit(value)
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValidationError(_("Enter a valid HTTPS URL."))


def validate_press_attachment_extension(upload):
    """Allow only PDF press attachments; do not accept executable formats."""
    if Path(upload.name).suffix.lower() != ".pdf":
        raise ValidationError(_("Press attachments must be PDF files."))
    if upload.size > MAX_PRESS_ATTACHMENT_SIZE:
        raise ValidationError(_("Press attachments must be 10 MB or smaller."))


def validate_press_attachment_content(upload):
    """Check PDF signatures in addition to the filename extension."""
    if not upload:
        return
    position = upload.tell()
    try:
        header = upload.read(5)
        upload.seek(max(upload.size - 1024, 0))
        tail = upload.read()
    finally:
        upload.seek(position)
    if header != b"%PDF-" or b"%%EOF" not in tail:
        raise ValidationError(_("Upload a valid PDF press attachment."))


def validate_press_cover(image):
    """Apply the shared image size limit to press-release covers."""
    validate_image_size(image)
