"""Licensed editorial crop imagery stored in Afribase Storage."""

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class CropImage(Base, TimestampMixin):
    __tablename__ = "crop_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    crop: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    image_url: Mapped[str] = mapped_column(String(500), nullable=False)
    alt: Mapped[str] = mapped_column(String(200), nullable=False)
    credit: Mapped[str] = mapped_column(String(200), nullable=False)
    source_url: Mapped[str] = mapped_column(String(500), nullable=False)
    license: Mapped[str] = mapped_column(String(80), nullable=False)
    changes: Mapped[str] = mapped_column(String(200), nullable=False)
    illustrative: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
