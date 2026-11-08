"""Store milestone photos outside the public static directory.

Files are named by their SHA-256. Callers serve them only through an authenticated route.
"""

from __future__ import annotations

import os
import re

_ROOT = "private-evidence"
_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
_NAME = re.compile(r"^[a-f0-9]{64}\.(jpg|png|webp)$")


def save(data: bytes, media_type: str, digest: str) -> str:
    """Write the photo once and return its filename, not a URL."""
    extension = _EXTENSIONS.get(media_type)
    if extension is None or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("unsupported evidence file")
    filename = f"{digest}.{extension}"
    path = _path(filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        handle = os.open(path, flags, 0o600)
        try:
            os.write(handle, data)
        finally:
            os.close(handle)
    return filename


def read(filename: str) -> bytes:
    """Read a stored photo. Raises ``FileNotFoundError`` for a missing or unsafe name."""
    path = _path(filename)
    with open(path, "rb") as handle:
        return handle.read()


def _path(filename: str) -> str:
    if not _NAME.fullmatch(filename or ""):
        raise FileNotFoundError(filename)
    root = os.path.abspath(_ROOT)
    path = os.path.abspath(os.path.join(root, filename))
    if os.path.commonpath([root, path]) != root:
        raise FileNotFoundError(filename)
    return path
