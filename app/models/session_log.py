from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SessionLog(Base):
    """Section 3.5. Login audit trail for support. Keep 90 days (see jobs/cleanup.py)."""

    __tablename__ = "sessions_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    mac_address: Mapped[str | None] = mapped_column(String(17), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    event: Mapped[str] = mapped_column(
        Enum("login_ok", "login_fail", "device_limit", "expired", name="session_event")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
