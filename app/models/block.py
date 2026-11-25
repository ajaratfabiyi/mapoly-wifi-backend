from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BlockedMac(Base):
    """A device that may not connect. Checked at login. Stays blocked even if the owner makes a new account."""

    __tablename__ = "blocked_macs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    mac: Mapped[str] = mapped_column(String(17), unique=True)
    reason: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
