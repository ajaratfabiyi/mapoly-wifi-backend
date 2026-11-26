from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AccountPlanCredit(Base):
    """Plan slots bought on an account that no device has taken yet.

    A purchase that covers more devices than are bound keeps the rest here. Each new device
    takes one slot when it is bound. See app/services/plan_credits.py.
    """

    __tablename__ = "account_plan_credits"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"))
    # When the plan ends. Fixed at purchase, so a device bound later gets only the time left.
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    slots_left: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime)
