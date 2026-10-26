"""Private file storage for participant submissions."""

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateSubmissionStorage(FileSystemStorage):
    """Store submissions outside public MEDIA_ROOT with no public URL."""

    def __init__(self, location=None, base_url=None):
        super().__init__(
            location=location or settings.PRIVATE_MEDIA_ROOT,
            base_url=base_url,
            file_permissions_mode=0o600,
            directory_permissions_mode=0o700,
        )

    def url(self, name):
        """Never produce a public URL for sensitive participant material."""
        raise ValueError("Private submission files do not have public URLs.")
