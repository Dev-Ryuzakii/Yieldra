"""Investment request/response schemas. Amounts are in Naira on the wire, kobo in the DB."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.investment import InvestmentStatus


class InvestmentCreate(BaseModel):
    farm_id: int
    investor_id: int
    amount_ngn: int = Field(gt=0, description="Investment amount in Naira")
    payment_reference: str


class InvestmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    farm_id: int
    investor_id: int
    amount_ngn: int = Field(description="Amount in kobo")
    shares: float
    expected_return_ngn: int
    actual_return_ngn: int
    status: InvestmentStatus
    created_at: datetime
