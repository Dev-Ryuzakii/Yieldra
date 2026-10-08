"""ORM models. Importing this package registers every table on ``Base.metadata``."""

from app.models.farm import Farm, FarmPlot, FarmStatus, PlotStatus
from app.models.harvest import (
    ColdStorageBooking,
    Harvest,
    HarvestStatus,
)
from app.models.investment import Investment, InvestmentStatus
from app.models.media import CropImage
from app.models.offtake import ContractStatus, OfftakeContract
from app.models.payout import DisbursementStatus, FarmerDisbursement, PayoutAccount
from app.models.reference import ColdStorageFacility, CropParameter
from app.models.sponsorship import (
    MilestoneStatus,
    Rail,
    Sponsorship,
    SponsorshipMilestone,
    SponsorshipStatus,
)
from app.models.user import Language, User, UserRole

__all__ = [
    "User",
    "UserRole",
    "Language",
    "Farm",
    "FarmPlot",
    "FarmStatus",
    "PlotStatus",
    "Harvest",
    "HarvestStatus",
    "ColdStorageBooking",
    "OfftakeContract",
    "ContractStatus",
    "Investment",
    "InvestmentStatus",
    "CropImage",
    "ColdStorageFacility",
    "CropParameter",
    "Sponsorship",
    "SponsorshipMilestone",
    "SponsorshipStatus",
    "MilestoneStatus",
    "Rail",
    "PayoutAccount",
    "FarmerDisbursement",
    "DisbursementStatus",
]
