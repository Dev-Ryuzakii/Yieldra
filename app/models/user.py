"""User model — farmers, investors, sponsors, buyers, logistics operators."""

import enum

from sqlalchemy import Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class UserRole(str, enum.Enum):
    farmer = "farmer"
    investor = "investor"
    buyer = "buyer"
    logistics = "logistics"
    sponsor = "sponsor"


class Language(str, enum.Enum):
    english = "english"
    yoruba = "yoruba"
    pidgin = "pidgin"
    hausa = "hausa"


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    # Sponsors who sign up on the web are identified by email; chat users have none.
    email: Mapped[str | None] = mapped_column(String(160), unique=True, index=True, nullable=True)
    afribase_uid: Mapped[str | None] = mapped_column(String(100), unique=True, index=True, nullable=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"), nullable=False
    )
    language_preference: Mapped[Language] = mapped_column(
        Enum(Language, name="language"), default=Language.english, nullable=False
    )

    # Relationships
    farms: Mapped[list["Farm"]] = relationship(
        back_populates="farmer", cascade="all, delete-orphan"
    )
    plots: Mapped[list["FarmPlot"]] = relationship(back_populates="investor")
    investments: Mapped[list["Investment"]] = relationship(back_populates="investor")
    contracts: Mapped[list["OfftakeContract"]] = relationship(back_populates="buyer")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.id} {self.name} ({self.role.value})>"
