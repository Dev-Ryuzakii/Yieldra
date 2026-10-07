"""Crop Vision Agent — multimodal crop photo diagnosis.

Model: settings.model_vision (multimodal). Analyses a farm photo and returns a diagnosis +
treatment recommendation in the farmer's language.
"""

from __future__ import annotations

from typing import Any

from app.agents.base import BaseAgent
from app.config import settings
from app.utils.logger import get_logger

log = get_logger("yieldra.agent.vision")

SYSTEM_PROMPT = (
    "You are Yieldra's Crop Vision Agent. You examine farm crop photos and identify "
    "diseases, pests, or nutrient problems. Give a short diagnosis and a practical, "
    "safe treatment recommendation. Respond in the user's language. Keep it to at most "
    "3 short sentences — farmers read on basic phones. Never recommend unsafe practices."
)


class VisionAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(model=settings.model_vision, system_prompt=SYSTEM_PROMPT)

    async def diagnose_crop_photo(
        self, photo_url: str, crop_type: str, farmer_language: str = "english"
    ) -> dict[str, Any]:
        """Analyse a crop photo via the multimodal model; return diagnosis text."""
        prompt = (
            f"This is a photo of a {crop_type} crop. Diagnose any disease, pest, or "
            f"deficiency and recommend treatment. Reply in {farmer_language}."
        )
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": photo_url}},
                ],
            }
        ]
        try:
            diagnosis = await self.run(messages)
        except Exception as exc:  # noqa: BLE001
            log.warning("vision diagnose failed: %s", exc)
            diagnosis = (
                "Could not analyse the photo right now. Please resend a clear, well-lit "
                "picture of the affected leaves."
            )
        log.info("crop diagnosed crop=%s lang=%s", crop_type, farmer_language)
        return {"crop_type": crop_type, "language": farmer_language, "diagnosis": diagnosis}
