from pydantic import BaseModel


class PlanOut(BaseModel):
    id: str
    title: str
    duration_hours: int
    price_naira: int
    speed_limit_mbps: int | None
    popular: bool
    points_award: int  # points earned when this plan is bought with cash
    points_cost: int | None  # points that buy it; null means points cannot buy it
    device_count: int  # how many of the account's devices one purchase covers
