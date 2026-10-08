"""Farmer payout obligations and historical bank account records.

A PayPal capture creates a pending obligation; settlement is not automated.
Legacy payment provider columns remain so older rows can be read.
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class DisbursementStatus(str, enum.Enum):
    pending_manual = "pending_manual"            # owed; no automated settlement rail configured
    needs_bank_details = "needs_bank_details"  # farmer has not added a bank account yet
    awaiting_funding = "awaiting_funding"      # historical funding record
    paid = "paid"                              # historical completed payout
    failed = "failed"                          # historical failed payout


class PayoutAccount(Base, TimestampMixin):
    __tablename__ = "payout_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), unique=True, index=True, nullable=False
    )
    bank_code: Mapped[str] = mapped_column(String(12), nullable=False)
    bank_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    account_number: Mapped[str] = mapped_column(String(20), nullable=False)
    # As verified by the bank through Tuago, not as typed by the farmer.
    account_name: Mapped[str] = mapped_column(String(160), nullable=False)
    tuago_subaccount_id: Mapped[str] = mapped_column(String(80), nullable=False)

    @property
    def masked(self) -> str:
        return f"****{self.account_number[-4:]}"


class FarmerDisbursement(Base, TimestampMixin):
    __tablename__ = "farmer_disbursements"

    id: Mapped[int] = mapped_column(primary_key=True)
    milestone_id: Mapped[int] = mapped_column(
        ForeignKey("sponsorship_milestones.id"), unique=True, index=True, nullable=False
    )
    farmer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    # What the sponsor paid for this tranche, and the rate used to convert it.
    source_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    source_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    rate: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    amount_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), default=DisbursementStatus.needs_bank_details.value, nullable=False
    )

    # The Tuago checkout Yieldra pays into, and the account to transfer to.
    tuago_session_id: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    tuago_reference: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    pay_bank_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    pay_account_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pay_account_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    pay_expires_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
