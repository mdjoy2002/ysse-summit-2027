"""Private storage for registration payment evidence."""

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivatePaymentProofStorage(FileSystemStorage):
    """Keep uploaded payment proof outside public media and without public URLs."""

    def __init__(self, location=None, base_url=None):
        super().__init__(
            location=location or settings.PRIVATE_MEDIA_ROOT,
            base_url=base_url,
            file_permissions_mode=0o600,
            directory_permissions_mode=0o700,
        )

    def url(self, name):
        raise ValueError("Payment proof files do not have public URLs.")
