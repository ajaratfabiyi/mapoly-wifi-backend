from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import UtcDatetime


class BulkGatedUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    csv: str = Field(min_length=1, max_length=300_000)


class GroupDisable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_ids: list[int] = Field(min_length=1, max_length=500)


class UserRow(BaseModel):
    id: int
    email: str
    full_name: str | None
    matric_no: str | None
    user_type: str
    access_type: str
    status: str
    is_active: bool
    is_admin: bool
    points: int
    max_devices: int | None
    gated_until: UtcDatetime = None


class UserPatch(BaseModel):
    """Leave a field out to keep it. Send max_devices as null for no limit."""

    model_config = ConfigDict(extra="forbid")

    is_active: bool | None = None
    max_devices: int | None = Field(default=None, ge=0, le=100)


class PasswordReset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temporary_password: str = Field(min_length=8, max_length=256)


class PointsAdjust(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delta: int = Field(ge=-1_000_000, le=1_000_000)
    reason: str = Field(min_length=3, max_length=200)

    @field_validator("delta")
    @classmethod
    def not_zero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("delta must not be zero")
        return value


class PointsEntryOut(BaseModel):
    id: int
    delta: int
    balance_after: int
    reason: str
    reference: str
    created_at: UtcDatetime = None


class PointsSummary(BaseModel):
    total_points: int
    accounts_with_points: int


class GrantPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(max_length=30)


class BlockCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mac: str = Field(max_length=32)
    reason: str = Field(min_length=3, max_length=200)


class BlockOut(BaseModel):
    id: int
    mac: str
    reason: str
    created_at: UtcDatetime = None


class OnlineSession(BaseModel):
    username: str | None
    mac_address: str | None
    ip_address: str | None
    started_at: datetime | None


class PaymentOut(BaseModel):
    reference: str
    email: str
    plan_title: str
    amount_naira: int
    status: str
    payment_method: str | None
    paid_at: UtcDatetime = None
    refunded_at: UtcDatetime = None
    created_at: UtcDatetime = None


class PlanAdminOut(BaseModel):
    id: str
    title: str
    duration_hours: int
    price_naira: int
    speed_limit_mbps: int | None
    points_award: int
    points_cost: int | None
    device_count: int
    is_active: bool


class PlanCreate(BaseModel):
    """A new plan for the storefront. The id is fixed once created."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=3, max_length=30, pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=2, max_length=100)
    duration_hours: int = Field(ge=1, le=24 * 366)
    price_kobo: int = Field(ge=0, le=10_000_000_000)
    speed_limit_mbps: int | None = Field(default=None, ge=1, le=10_000)
    points_award: int = Field(default=0, ge=0, le=1_000_000)
    points_cost: int | None = Field(default=None, ge=0, le=10_000_000)
    device_count: int = Field(default=1, ge=1, le=50)
    is_active: bool = True


class PlanPatch(BaseModel):
    """Leave a field out to keep it. speed_limit_mbps and points_cost may be sent as null."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=2, max_length=100)
    price_kobo: int | None = Field(default=None, ge=0, le=10_000_000_000)  # up to ₦100M
    speed_limit_mbps: int | None = Field(default=None, ge=1, le=10_000)
    points_award: int | None = Field(default=None, ge=0, le=1_000_000)
    points_cost: int | None = Field(default=None, ge=0, le=10_000_000)
    device_count: int | None = Field(default=None, ge=1, le=50)
    is_active: bool | None = None


class AnnouncementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=3, max_length=300)


class AnnouncementPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool


class AnnouncementOut(BaseModel):
    id: int
    message: str
    active: bool
    created_at: UtcDatetime = None


class PublicAnnouncement(BaseModel):
    id: int
    message: str
    created_at: UtcDatetime = None


class AuditOut(BaseModel):
    created_at: UtcDatetime = None
    admin_email: str | None
    action: str
    target_user_id: int | None
    detail: str | None


class LoginEventOut(BaseModel):
    created_at: UtcDatetime = None
    event: str
    user_id: int | None
    mac_address: str | None
    ip_address: str | None


class ReportOut(BaseModel):
    days: int
    payments_count: int
    payments_naira_total: int
    new_users: int
    total_users: int
    gated_users: int
    devices_seen_15_min: int
    devices_connected_now: int
