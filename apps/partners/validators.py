"""Server-side validation for sponsor media and external links."""

from PIL import Image, UnidentifiedImageError
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, URLValidator
from django.utils.translation import gettext_lazy as _


MAX_SPONSOR_DECK_SIZE = 20 * 1024 * 1024
validate_sponsor_website = URLValidator(schemes=["https"])
validate_sponsor_deck_extension = FileExtensionValidator(["pdf"])


def validate_sponsorship_deck(upload):
    """Allow only bounded PDF files with PDF signatures, not renamed executables."""
    if upload.size > MAX_SPONSOR_DECK_SIZE:
        raise ValidationError(_("The sponsorship deck must be 20 MB or smaller."))

    file_position = upload.tell()
    try:
        upload.seek(0)
        header = upload.read(8)
        upload.seek(max(upload.size - 2048, 0))
        trailer = upload.read()
    finally:
        upload.seek(file_position)

    if not header.startswith(b"%PDF-") or b"%%EOF" not in trailer:
        raise ValidationError(_("Upload a valid PDF sponsorship deck."))


def validate_sponsor_logo(image):
    """Verify logo content with Pillow, not only its filename or browser MIME."""
    position = image.tell()
    try:
        image.seek(0)
        with Image.open(image) as opened_image:
            if opened_image.width * opened_image.height > 40_000_000:
                raise ValidationError(_("Sponsor logos cannot exceed 40 megapixels."))
            opened_image.verify()
    except (
        Image.DecompressionBombError,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as error:
        raise ValidationError(_("Upload a valid image file for the sponsor logo.")) from error
    finally:
        image.seek(position)
