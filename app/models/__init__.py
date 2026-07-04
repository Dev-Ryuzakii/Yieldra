"""ORM models. Importing this package registers every table on ``Base.metadata``."""

from app.models.farm import Farm, FarmPlot, FarmStatus, PlotStatus
from app.models.harvest import (
    ColdStorageBooking,
    Harvest,
    HarvestStatus,
)
from app.models.investment import Investment, InvestmentStatus
from app.models.offtake import ContractStatus, OfftakeContract
from app.models.reference import ColdStorageFacility, CropParameter
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
    "ColdStorageFacility",
    "CropParameter",
]
