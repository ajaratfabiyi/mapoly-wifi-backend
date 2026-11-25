from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Announcement(Base):
    """A message shown on the portal, such as a maintenance notice. Only active ones are shown."""

    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    message: Mapped[str] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
