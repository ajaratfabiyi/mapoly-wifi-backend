from sqlalchemy import BigInteger, Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Plan(Base):
    """Section 3.3. Prices are stored in kobo (₦400 = 40000)."""

    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(30), primary_key=True)
    title: Mapped[str] = mapped_column(String(100))
    duration_hours: Mapped[int] = mapped_column(Integer)
    price_kobo: Mapped[int] = mapped_column(BigInteger)
    # Informational. Enforced by the RADIUS group, not by this table.
    # Nullable until the speed question in Section 11 is settled.
    speed_limit_mbps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Points the buyer earns when this plan is paid for with cash.
    points_award: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Points needed to buy this plan with points (1 point = ₦1). None means points cannot buy it.
    points_cost: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # How many of the account's devices one purchase covers. 1 means the plan is for one device.
    device_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
