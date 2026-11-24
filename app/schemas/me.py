from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.common import UtcDatetime


class DeviceOut(BaseModel):
    id: int
    name: str | None
    mac_address: str
    ip_address: str | None
    bound_plan: str | None
    expires_at: UtcDatetime = None
    auto_renew: bool
    status: str
    last_seen: UtcDatetime = None


class MeResponse(BaseModel):
    email: str
    user_type: str
    matric_no: str | None
    department: str | None
    level: str | None
    phone: str | None
    plan_name: str | None
    access_expires_at: UtcDatetime = None
    days_left: int | None
    auto_renew: bool
    max_devices: int | None  # null means no limit
    points: int  # points balance, 1 point = ₦1 when spent
    devices: list[DeviceOut]


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str | None = Field(default=None, max_length=20)
    email: EmailStr | None = None
