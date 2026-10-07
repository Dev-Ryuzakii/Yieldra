"""Keeps a copy of each farm photo that was judged, so sponsors and reviewers can see it.

Photos are stored under ``generated/evidence/`` (served at ``/static/evidence/``) and
named by their SHA-256, which makes the URL unguessable and storage idempotent. On a
host with an ephemeral disk the files are lost on redeploy; point ``generated/`` at a
persistent disk (or swap this module for object storage) before relying on them.
"""

from __future__ import annotations

import os

_ROOT = "generated"
_FOLDER = "evidence"
_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


def save(data: bytes, media_type: str, digest: str) -> str:
    """Write the photo once and return its path relative to ``generated/``."""
    extension = _EXTENSIONS.get(media_type, "jpg")
    relative = f"{_FOLDER}/{digest}.{extension}"
    path = os.path.join(_ROOT, relative)
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
    return relative


def url_for(relative: str | None) -> str | None:
    return f"/static/{relative}" if relative else None
