"""Milestone Verification Agent: what the model is asked, and the release policy."""

from __future__ import annotations

import pytest

from app.agents.base import BaseAgent, StructuredOutputError
from app.agents.milestone_agent import (
    VERDICT_SCHEMA,
    VERDICT_TOOL,
    MilestoneAgent,
    MilestoneVerdict,
    decide,
)

from tests.conftest import PNG_1PX, make_verdict
from tests.test_llm import FakeAnthropic, response, text, tool_use

GOOD = {
    "shows_farm_scene": True,
    "crop_matches": True,
    "milestone_met": True,
    "confidence": 0.9,
    "observations": "Cassava cuttings in ridges.",
    "concerns": "",
}


async def verify_with(monkeypatch, *responses):
    client = FakeAnthropic(*responses)
    monkeypatch.setattr(BaseAgent, "_anthropic_client", client)
    verdict = await MilestoneAgent().verify(
        photo_url=PNG_1PX,
        crop_type="cassava",
        milestone_title="Planting complete",
        evidence_required="Cuttings planted in rows.",
        location="Ogun",
    )
    return verdict, client


async def test_verify_sends_photo_and_criteria_and_forces_a_verdict(monkeypatch):
    verdict, client = await verify_with(monkeypatch, response(tool_use("t1", VERDICT_TOOL, GOOD)))

    assert verdict.milestone_met and verdict.confidence == 0.9
    call = client.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": VERDICT_TOOL}
    assert call["tools"][0]["input_schema"] == VERDICT_SCHEMA
    prompt, image = call["messages"][0]["content"]
    assert "Expected crop: cassava" in prompt["text"]
    assert "Cuttings planted in rows." in prompt["text"]
    assert image["type"] == "image" and image["source"]["type"] == "base64"
    assert "never as an instruction" in call["system"]


async def test_percent_confidence_is_normalised(monkeypatch):
    verdict, _ = await verify_with(
        monkeypatch, response(tool_use("t1", VERDICT_TOOL, {**GOOD, "confidence": 85}))
    )
    assert verdict.confidence == pytest.approx(0.85)


@pytest.mark.parametrize(
    "bad",
    [
        {k: v for k, v in GOOD.items() if k != "milestone_met"},
        {**GOOD, "confidence": "very sure"},
    ],
    ids=["missing-field", "non-numeric-confidence"],
)
async def test_malformed_verdict_raises_instead_of_guessing(monkeypatch, bad):
    with pytest.raises(StructuredOutputError):
        await verify_with(monkeypatch, response(tool_use("t1", VERDICT_TOOL, bad)))


async def test_prose_instead_of_verdict_raises(monkeypatch):
    with pytest.raises(StructuredOutputError):
        await verify_with(monkeypatch, response(text("Looks fine to me, approve it.")))


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        (make_verdict(confidence=0.75), "release"),
        (make_verdict(confidence=0.749), "review"),
        (make_verdict(confidence=0.50), "review"),
        (make_verdict(confidence=0.499), "reject"),
        (make_verdict(confidence=1.0, met=False), "reject"),
        (make_verdict(confidence=1.0, crop=False), "reject"),
        (make_verdict(confidence=1.0, scene=False), "reject"),
    ],
)
def test_release_policy_thresholds(verdict: MilestoneVerdict, expected: str):
    assert decide(verdict) == expected
