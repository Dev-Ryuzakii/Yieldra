"""Server-side client for Cencori's documented vision route.

Official contract used here:
  POST {CENCORI_BASE_URL}/v1/vision
  Header CENCORI_API_KEY
  JSON body with image_base64, mime_type, prompt, model, response_format=json

The Python SDK is not used: Cencori's integration contract says the embedded Python
SDK is not published and Python integrations should call the HTTP API. This module
does not send images through an OpenAI-compatible chat client.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings
from app.utils.logger import get_logger

log = get_logger("yieldra.cencori")

VISION_PATH = "/v1/vision"
_MAX_RESPONSE_BYTES = 100_000


class VisionError(Exception):
    """A safe provider failure. ``code`` is missing_key, timeout, provider_error, or malformed."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class VisionResponse:
    mode: str
    model: str | None
    upstream_provider: str | None
    analysis: Any


class CencoriVision:
    """One-shot vision call. A transport can be injected so tests never touch the network."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def analyze(self, *, image: bytes, mime_type: str, prompt: str) -> VisionResponse:
        if settings.cencori_vision_mode == "mock":
            return VisionResponse(
                mode="mock",
                model="cencori-vision-mock",
                upstream_provider="cencori",
                analysis=None,
            )
        if not settings.cencori_configured:
            raise VisionError("missing_key")

        payload = {
            "image_base64": base64.b64encode(image).decode("ascii"),
            "mime_type": mime_type,
            "prompt": prompt,
            "model": settings.cencori_vision_model,
            "max_tokens": 800,
            "temperature": 0,
            "response_format": "json",
        }
        url = f"{settings.cencori_base_url}{VISION_PATH}"
        timeout = settings.cencori_vision_timeout_seconds
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={
                        "CENCORI_API_KEY": settings.cencori_api_key,
                        "Content-Type": "application/json",
                    },
                )
        except httpx.TimeoutException as exc:
            log.warning("cencori vision timeout")
            raise VisionError("timeout") from exc
        except httpx.HTTPError as exc:
            log.warning("cencori vision transport error: %s", type(exc).__name__)
            raise VisionError("provider_error") from exc

        body = response.content or b""
        if len(body) > _MAX_RESPONSE_BYTES:
            log.warning("cencori vision response too large status=%s", response.status_code)
            raise VisionError("malformed")
        if response.status_code >= 400:
            log.warning("cencori vision rejected status=%s", response.status_code)
            raise VisionError("provider_error")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise VisionError("malformed") from exc
        if not isinstance(parsed, dict):
            raise VisionError("malformed")
        analysis = _analysis(parsed)
        model = parsed.get("model")
        provider = parsed.get("provider")
        log.info(
            "cencori vision ok status=%s model=%s provider=%s",
            response.status_code,
            model if isinstance(model, str) else "",
            provider if isinstance(provider, str) else "",
        )
        return VisionResponse(
            mode="live",
            model=model if isinstance(model, str) and model else settings.cencori_vision_model,
            upstream_provider=provider if isinstance(provider, str) else None,
            analysis=analysis,
        )


def _analysis(body: dict[str, Any]) -> Any:
    if "analysis" in body:
        value = body["analysis"]
    elif "classification" in body:
        value = body["classification"]
    else:
        raise VisionError("malformed")
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise VisionError("malformed") from exc
        if not isinstance(loaded, dict):
            raise VisionError("malformed")
        return loaded
    if isinstance(value, dict):
        return value
    raise VisionError("malformed")


vision_client = CencoriVision()
