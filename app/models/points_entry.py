from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PointsEntry(Base):
    """One change to a user's points balance. Positive when points are earned,
    negative when they are spent. `reference` links it to the purchase or redemption."""

    __tablename__ = "points_entries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    delta: Mapped[int] = mapped_column(Integer)
    balance_after: Mapped[int] = mapped_column(Integer)
    # "purchase" (earned on a cash payment) or "redeem" (spent on a plan).
    reason: Mapped[str] = mapped_column(String(20))
    reference: Mapped[str] = mapped_column(String(100), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
