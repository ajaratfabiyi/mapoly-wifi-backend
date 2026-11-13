from pydantic import BaseModel


class PlanOut(BaseModel):
    id: str
    title: str
    duration_hours: int
    price_naira: int
    speed_limit_mbps: int | None
    popular: bool
