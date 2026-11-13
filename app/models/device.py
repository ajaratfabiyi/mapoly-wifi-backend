from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class UserDevice(Base):
    """Section 3.2. mac_address is stored as AA:BB:CC:DD:EE:FF (uppercase, colons).

    A global UNIQUE on mac_address also guarantees (user_id, mac_address) is unique,
    so the composite constraint in the draft is not added separately.
    """

    __tablename__ = "user_devices"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    mac_address: Mapped[str] = mapped_column(String(17), unique=True)
    device_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=True)

    # This device's own plan. Paid accounts only. NULL means this device has no plan.
    # Each device's plan works only on that device.
    access_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # The access_expires_at value the expiry job last handled, so each expiry runs once.
    expiry_handled_for: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    bound_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="devices")


from app.models.user import User  # noqa: E402
