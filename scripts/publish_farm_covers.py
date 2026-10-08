"""Publish licensed illustrative crop covers to Afribase Storage.

Run after creating the public ``farm-covers`` bucket. Credentials are read from
.env or the environment; only public URLs and attribution are written to data/.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
from urllib.parse import quote

import httpx
from dotenv import dotenv_values
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
BUCKET = "farm-covers"
IMAGES = (
    {"crop": "cassava", "file": "Cassava plantation farm.jpg", "author": "Ghabzman",
     "license": "CC BY 4.0", "alt": "Cassava plants growing in a field in Nigeria"},
    {"crop": "maize", "file": "Cassava and maize farm.jpg", "author": "Nubelbariloe",
     "license": "CC BY 4.0", "alt": "A Nigerian field with cassava and maize plants"},
    {"crop": "tomatoes", "file": "Fresh Tomatoes from the Nigeria Soil 2.jpg", "author": "Eze kelechi",
     "license": "CC0", "alt": "Fresh tomatoes harvested in Nigeria"},
    {"crop": "rice", "file": "Rice farming in Nigeria.jpg", "author": "MrUbash",
     "license": "CC0", "alt": "Rice farming in Gombe State, Nigeria"},
    {"crop": "groundnuts", "file": "Groundnuts farm1.jpg", "author": "Kingmoh1",
     "license": "CC0", "alt": "Groundnuts growing on a Nigerian farm"},
    {"crop": "peppers", "file": "Picture of harvested peper.jpg", "author": "Nubelbariloe",
     "license": "CC BY 4.0", "alt": "Fresh peppers harvested from a farm in Nigeria"},
)


def main() -> None:
    values = dotenv_values(ROOT / ".env")
    base = (os.getenv("AFRIBASE_URL") or values.get("AFRIBASE_URL") or "").rstrip("/")
    key = os.getenv("AFRIBASE_SERVICE_ROLE_KEY") or values.get("AFRIBASE_SERVICE_ROLE_KEY") or ""
    if not base or not key:
        raise SystemExit("Set AFRIBASE_URL and AFRIBASE_SERVICE_ROLE_KEY in .env")
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    output: list[dict] = []
    with httpx.Client(timeout=30, headers={"User-Agent": "Yieldra/1.0 (licensed farm covers)"}) as public:
        with httpx.Client(timeout=30) as storage:
            for item in IMAGES:
                metadata = public.get("https://commons.wikimedia.org/w/api.php", params={
                    "action": "query", "format": "json", "prop": "imageinfo",
                    "iiprop": "url|extmetadata", "iiurlwidth": "1200",
                    "titles": f"File:{item['file']}",
                })
                metadata.raise_for_status()
                info = next(iter(metadata.json()["query"]["pages"].values()))["imageinfo"][0]
                license_name = info["extmetadata"]["LicenseShortName"]["value"]
                if license_name != item["license"]:
                    raise RuntimeError(f"License changed for {item['file']}: {license_name}")
                download = public.get(info["thumburl"])
                download.raise_for_status()
                image = ImageOps.exif_transpose(Image.open(io.BytesIO(download.content))).convert("RGB")
                image.thumbnail((1200, 1200))
                buffer = io.BytesIO()
                image.save(buffer, format="WEBP", quality=82, method=6)
                path = f"{item['crop']}-illustrative.webp"
                result = storage.post(f"{base}/storage/v1/object/{BUCKET}/{quote(path)}",
                    headers={**headers, "Content-Type": "image/webp", "x-upsert": "true"},
                    content=buffer.getvalue())
                if result.status_code not in (200, 201):
                    raise RuntimeError(f"Upload failed for {item['crop']}: HTTP {result.status_code}: {result.text[:200]}")
                public_url = f"{base}/storage/v1/object/public/{BUCKET}/{path}"
                check = public.get(public_url)
                check.raise_for_status()
                output.append({"crop": item["crop"], "url": public_url,
                    "alt": item["alt"], "credit": item["author"], "license": item["license"],
                    "source": info["descriptionurl"], "changes": "Resized and converted to WebP",
                    "illustrative": True})
                print(f"Uploaded {item['crop']} cover ({len(buffer.getvalue()) // 1024} KiB)")
    target = ROOT / "data" / "farm_covers.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(output, indent=2) + "\n")
    print(f"Saved public URLs and attribution to {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
