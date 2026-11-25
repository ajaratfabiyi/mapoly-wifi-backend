from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AdminAction(Base):
    """An audit row for every change an admin makes. Lets ICT see who changed what, and when."""

    __tablename__ = "admin_actions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    admin_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Such as "create_gated_user" or "set_gated_end_date".
    action: Mapped[str] = mapped_column(String(40))
    target_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Short, human-readable detail. Never a password.
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
