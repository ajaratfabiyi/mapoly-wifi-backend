from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Plan
from app.schemas.plans import PlanOut

router = APIRouter(prefix="/api", tags=["plans"])

# Shown as the highlighted plan on the storefront (see the draft's example in 5.8).
POPULAR_PLAN_ID = "plan_weekly"


@router.get("/plans", response_model=list[PlanOut])
def list_plans(db: Session = Depends(get_db)) -> list[PlanOut]:
    plans = db.scalars(
        select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.duration_hours)
    ).all()
    return [
        PlanOut(
            id=p.id,
            title=p.title,
            duration_hours=p.duration_hours,
            price_naira=p.price_kobo // 100,
            speed_limit_mbps=p.speed_limit_mbps,
            popular=p.id == POPULAR_PLAN_ID,
        )
        for p in plans
    ]
