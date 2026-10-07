"""Naira payout models — how a farmer actually receives money.

``PayoutAccount`` is the farmer's bank account, registered with Tuago as a
subaccount so collections routed through it settle to that bank.

``FarmerDisbursement`` is the naira owed to a farmer for one PayPal-funded tranche.
Tuago cannot send money, so Yieldra pays it *in* through a Tuago checkout routed to
the farmer's subaccount; Tuago verifies the transfer and settles it to the farmer.
Naira-sponsored tranches need no disbursement: the sponsor's own payment is already
routed through the farmer's subaccount.
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class DisbursementStatus(str, enum.Enum):
    needs_bank_details = "needs_bank_details"  # farmer has not added a bank account yet
    awaiting_funding = "awaiting_funding"      # Tuago account issued; Yieldra must transfer
    paid = "paid"                              # Tuago confirmed the transfer
    failed = "failed"                          # the Tuago payment failed or expired


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
