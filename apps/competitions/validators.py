"""Validators for private participant-submitted files."""

import os
import zipfile
import zlib

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


FILE_SIGNATURES = {
    ".pdf": lambda header: header.startswith(b"%PDF-"),
    ".png": lambda header: header.startswith(b"\x89PNG\r\n\x1a\n"),
    ".jpg": lambda header: header.startswith(b"\xff\xd8\xff"),
    ".jpeg": lambda header: header.startswith(b"\xff\xd8\xff"),
    ".mp4": lambda header: len(header) >= 8 and header[4:8] == b"ftyp",
    ".webm": lambda header: header.startswith(b"\x1aE\xdf\xa3"),
}


def _valid_office_document(uploaded_file, extension):
    """Check OOXML archive contents rather than trusting a ZIP extension."""
    try:
        with zipfile.ZipFile(uploaded_file) as archive:
            names = set(archive.namelist())
            required_member = {
                ".docx": "word/document.xml",
                ".pptx": "ppt/presentation.xml",
            }[extension]
            if "[Content_Types].xml" not in names or required_member not in names:
                return False
            with archive.open("[Content_Types].xml") as content_type_file:
                content_types = content_type_file.read(8192).lower()
            if extension == ".docx":
                return b"wordprocessingml" in content_types
            return b"presentationml" in content_types
    except (
        EOFError,
        KeyError,
        NotImplementedError,
        OSError,
        RuntimeError,
        ValueError,
        zipfile.BadZipFile,
        zlib.error,
    ):
        return False
    finally:
        uploaded_file.seek(0)


def validate_submission_file(uploaded_file, allowed_extensions, max_size_mb):
    """Validate size, allowlisted extension, and file content signature."""
    extension = os.path.splitext(uploaded_file.name)[1].lower()
    allowed = {
        str(value).lower() if str(value).startswith(".") else f".{str(value).lower()}"
        for value in allowed_extensions
    }
    if extension not in allowed:
        raise ValidationError(
            _("This competition does not accept the %(extension)s file type."),
            params={"extension": extension or _("unknown")},
        )

    if uploaded_file.size > max_size_mb * 1024 * 1024:
        raise ValidationError(
            _("Files must be %(limit)s MB or smaller."),
            params={"limit": max_size_mb},
        )

    if extension in {".docx", ".pptx"}:
        valid_signature = _valid_office_document(uploaded_file, extension)
    else:
        signature_check = FILE_SIGNATURES.get(extension)
        header = uploaded_file.read(16)
        uploaded_file.seek(0)
        valid_signature = signature_check is not None and signature_check(header)

    if not valid_signature:
        raise ValidationError(
            _("The uploaded file content does not match its extension.")
        )


def validate_rulebook_file(uploaded_file):
    """Require downloadable rulebooks to be real, reasonably sized PDFs."""
    validate_submission_file(uploaded_file, [".pdf"], 20)
