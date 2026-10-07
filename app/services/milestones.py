"""Milestone plans for sponsorships: what gets paid when, and on what evidence.

Every sponsorship is split into four tranches. The first is paid when the sponsor
approves (it funds inputs and land preparation). Each later tranche needs a photo
from the farmer showing the stage described in ``evidence``.
"""

from __future__ import annotations

from dataclasses import dataclass

# Shares in basis points (10000 = 100%). They must add up to 10000.
_KICKOFF_BPS = 2000
_PLANTED_BPS = 3000
_ESTABLISHED_BPS = 3000
_HARVEST_BPS = 2000

MIN_TOTAL_MINOR = 1_000        # $10.00
MAX_TOTAL_MINOR = 1_000_000    # $10,000.00


@dataclass(frozen=True)
class MilestoneTemplate:
    key: str
    title: str
    share_bps: int
    # None for the kickoff tranche, which is paid on sponsor approval.
    evidence: str | None


# Evidence wording per crop for the three photo-verified stages.
_CROP_EVIDENCE: dict[str, dict[str, str]] = {
    "cassava": {
        "planted": (
            "A cleared field with cassava stem cuttings planted in rows, ridges or mounds. "
            "The cuttings or very young shoots should be visible across the plot."
        ),
        "established": (
            "Cassava plants around three months old: knee-to-waist height with the "
            "characteristic palmate (hand-shaped) leaves forming a canopy over most of the plot."
        ),
        "harvest": (
            "Harvested cassava: uprooted plants or a pile of cassava tubers (long brown "
            "roots) at the farm."
        ),
    },
    "maize": {
        "planted": (
            "A cleared field with maize sown in rows; seedlings just emerging or freshly "
            "sown rows clearly visible across the plot."
        ),
        "established": (
            "Maize plants at knee height or taller, growing in rows with long strap-shaped "
            "leaves covering most of the plot."
        ),
        "harvest": "Harvested maize: cobs picked and piled, or dry stalks with cobs being picked.",
    },
    "tomatoes": {
        "planted": (
            "Tomato seedlings transplanted into beds or rows across the plot, with staking "
            "or watering in progress."
        ),
        "established": (
            "Tomato plants with a full canopy, flowering or carrying green fruit, across "
            "most of the plot."
        ),
        "harvest": "Harvested tomatoes: ripe fruit picked into baskets or crates at the farm.",
    },
}

_GENERIC_EVIDENCE: dict[str, str] = {
    "planted": "A cleared, prepared field planted with {crop} across the plot.",
    "established": "Well-established {crop} covering most of the plot.",
    "harvest": "Harvested {crop} gathered at the farm.",
}


def plan_for_crop(crop_type: str) -> list[MilestoneTemplate]:
    """The four-tranche plan for a crop, in order."""
    crop = (crop_type or "").strip().lower()
    evidence = _CROP_EVIDENCE.get(crop) or {
        stage: text.format(crop=crop or "crops") for stage, text in _GENERIC_EVIDENCE.items()
    }
    return [
        MilestoneTemplate(
            "kickoff", "Sponsorship confirmed: inputs and land preparation", _KICKOFF_BPS, None
        ),
        MilestoneTemplate("planted", "Planting complete", _PLANTED_BPS, evidence["planted"]),
        MilestoneTemplate(
            "established", "Crop established", _ESTABLISHED_BPS, evidence["established"]
        ),
        MilestoneTemplate("harvest", "Harvest", _HARVEST_BPS, evidence["harvest"]),
    ]


def split_amount(total_minor: int, shares_bps: list[int]) -> list[int]:
    """Split ``total_minor`` by basis-point shares. The parts always sum to the total."""
    if sum(shares_bps) != 10_000:
        raise ValueError("shares must add up to 10000 basis points")
    parts = [total_minor * bps // 10_000 for bps in shares_bps]
    # Rounding leftovers go to the last tranche so nothing is lost or over-charged.
    parts[-1] += total_minor - sum(parts)
    return parts
