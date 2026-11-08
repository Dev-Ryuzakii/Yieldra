"""Validate uploaded milestone photos by size and file signature.

The declared multipart type is ignored. EXIF is not read and is not treated as proof.
"""

from __future__ import annotations

MAX_IMAGE_BYTES = 5 * 1024 * 1024

_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)


def validate_image(data: bytes) -> str:
    """Return the sniffed media type, or raise ``ValueError`` with a safe message."""
    if not data:
        raise ValueError("The file is empty.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("The photo is larger than 5 MB.")
    media = _sniff(data)
    if media is None:
        raise ValueError("The file must be a JPEG, PNG, or WebP image.")
    return media


def _sniff(data: bytes) -> str | None:
    for signature, media in _SIGNATURES:
        if data.startswith(signature):
            return media
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None
