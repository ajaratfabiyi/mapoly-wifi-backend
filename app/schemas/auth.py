from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_type: Literal["student", "lecturer", "guest", "gated"]
    email: EmailStr
    password: str = Field(min_length=6, max_length=256)
    # Optional. Without a MAC the account is created and the device is added later.
    mac: str | None = Field(default=None, max_length=32)
    ip: str | None = Field(default=None, max_length=45)
    deviceName: str | None = Field(default=None, max_length=100)
    matric_no: str | None = Field(default=None, max_length=50)


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(max_length=256)
    new_password: str = Field(min_length=6, max_length=256)


class MessageResponse(BaseModel):
    ok: bool
    message: str | None = None
