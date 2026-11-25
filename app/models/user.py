from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class User(Base):
    """Section 3.1."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[str] = mapped_column(
        Enum("pre_registered", "active", "disabled", name="user_status"),
        default="pre_registered",
    )
    user_type: Mapped[str] = mapped_column(
        Enum("student", "lecturer", "guest", "gated", name="user_type"),
    )

    full_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    matric_no: Mapped[str | None] = mapped_column(String(50), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)
    level: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Devices this account may bind. None means no limit.
    max_devices: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    access_type: Mapped[str] = mapped_column(
        Enum("paid", "free", name="access_type"),
        default="paid",
    )
    # Access is per device (user_devices.access_expires_at). Free users need no expiry.
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Points balance. 1 point = ₦1 when spent. Every change is also written to points_entries.
    points: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Set by an admin. Admins can open the admin routes.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # Set when an admin creates the account with a temporary password. Cleared on change.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    # Gated accounts only: when their access ends. None means no end date.
    gated_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    devices: Mapped[list["UserDevice"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    ledger_entries: Mapped[list["Ledger"]] = relationship(back_populates="user")


# Imported at the bottom to avoid circular imports between models.
from app.models.device import UserDevice  # noqa: E402
from app.models.ledger import Ledger  # noqa: E402
