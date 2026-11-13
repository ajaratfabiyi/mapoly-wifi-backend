from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import UtcDatetime


class InitializeRequest(BaseModel):
    """The server uses the signed-in user's email, so any `email` sent is ignored."""

    model_config = ConfigDict(extra="ignore")

    plan_id: str = Field(max_length=30)
    device_id: int | None = None


class InitializeResponse(BaseModel):
    reference: str
    authorization_url: str
    access_code: str


class VerifyResponse(BaseModel):
    status: str
    access_expires_at: UtcDatetime = None
