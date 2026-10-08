"""User request/response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.user import Language, UserRole


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=6, max_length=20)
    role: UserRole
    language_preference: Language = Language.english


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    phone: str
    email: str | None
    role: UserRole
    language_preference: Language
    created_at: datetime
