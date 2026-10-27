"""Local copies of the licensed demo covers when Afribase Storage is unavailable."""

import json
from pathlib import Path

from app.models.media import CropImage

_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST = json.loads((_ROOT / "data" / "farm_covers.json").read_text())
_COVERS = {item["crop"]: item for item in _MANIFEST}


def cover_for_crop(crop: str, image: CropImage | None = None) -> dict | None:
    """Return a cover and attribution, replacing only known broken seed URLs."""
    seed = _COVERS.get(crop)
    if image is None and seed is None:
        return None

    if image is not None:
        url = image.image_url
        if seed and url == seed["url"]:
            url = f"/assets/farm-covers/{crop}-illustrative.webp"
        return {"url": url, "alt": image.alt, "credit": image.credit,
                "source": image.source_url, "license": image.license,
                "changes": image.changes, "illustrative": image.illustrative}

    return {"url": f"/assets/farm-covers/{crop}-illustrative.webp",
            "alt": seed["alt"], "credit": seed["credit"],
            "source": seed["source"], "license": seed["license"],
            "changes": seed["changes"], "illustrative": seed["illustrative"]}


def known_crops() -> tuple[str, ...]:
    return tuple(_COVERS)
