from pydantic import BaseModel, Field

from app.schemas.common import UtcDatetime


class RedeemRequest(BaseModel):
    plan_id: str = Field(max_length=30)
    device_id: int


class RedeemResponse(BaseModel):
    status: str
    reference: str
    points_balance: int
    access_expires_at: UtcDatetime = None
