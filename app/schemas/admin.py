from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.schemas.common import UtcDatetime


def _naive_utc(value: datetime | None) -> datetime | None:
    """Store times as naive UTC, the same as the rest of the database."""
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class GatedUserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    full_name: str = Field(min_length=2, max_length=100)
    matric_no: str | None = Field(default=None, max_length=50)
    department: str | None = Field(default=None, max_length=100)
    level: str | None = Field(default=None, max_length=20)
    phone: str | None = Field(default=None, max_length=20)
    temporary_password: str = Field(min_length=8, max_length=256)
    # Optional. Give an end date, or a duration in days from now, or neither (no end date).
    gated_until: datetime | None = None
    duration_days: int | None = Field(default=None, ge=1, le=3650)

    @field_validator("gated_until")
    @classmethod
    def normalise_until(cls, value: datetime | None) -> datetime | None:
        return _naive_utc(value)

    @model_validator(mode="after")
    def one_way_to_set_the_end(self):
        if self.gated_until is not None and self.duration_days is not None:
            raise ValueError("Give either an end date or a duration, not both.")
        return self


class GatedUserUpdate(BaseModel):
    """Set an end date (gated_until), or extend by a duration from now (duration_days).
    Send gated_until as null to remove the end date. Leave both out to keep it as it is."""

    model_config = ConfigDict(extra="forbid")

    gated_until: datetime | None = None
    duration_days: int | None = Field(default=None, ge=1, le=3650)

    @field_validator("gated_until")
    @classmethod
    def normalise_until(cls, value: datetime | None) -> datetime | None:
        return _naive_utc(value)

    @model_validator(mode="after")
    def one_way_to_set_the_end(self):
        if "gated_until" in self.model_fields_set and self.duration_days is not None:
            raise ValueError("Give either an end date or a duration, not both.")
        return self


class GatedUserOut(BaseModel):
    id: int
    email: str
    full_name: str | None
    matric_no: str | None
    department: str | None
    level: str | None
    phone: str | None
    gated_until: UtcDatetime = None
    must_change_password: bool
