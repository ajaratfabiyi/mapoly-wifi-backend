from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.common import UtcDatetime


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=6, max_length=256)
    # Optional. Sign-in without a MAC opens the dashboard only; the device is bound on a later
    # sign-in that carries its MAC.
    mac: str | None = Field(default=None, max_length=32)
    ip: str | None = Field(default=None, max_length=45)
    deviceName: str | None = Field(default=None, max_length=100)


class LoginResponse(BaseModel):
    username: str
    token: str | None
    expires_in: int
    has_active_plan: bool
    access_expires_at: UtcDatetime = None
