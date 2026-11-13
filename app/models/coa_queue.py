from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CoaQueue(Base):
    """Disconnects waiting to be sent to the router by the CoA worker on the AAA VM.

    The backend writes a row. It never contacts the router, which has a private
    address the backend cannot reach. The worker sends the disconnect and sets sent_at.
    """

    __tablename__ = "coa_queue"

    # SQLite only auto-increments a plain INTEGER primary key, so BIGINT is used on MySQL only.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    reason: Mapped[str] = mapped_column(
        Enum(
            "plan_expired",
            "device_removed",
            "device_plan_expired",
            "account_disabled",
            name="coa_reason",
        )
    )
    # plan_expired and account_disabled: the account email (matches every device username).
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # device_removed and device_plan_expired: the MAC in RADIUS format (AA-BB-CC-DD-EE-FF),
    # as Calling-Station-Id. Only this device's session is disconnected.
    mac_address: Mapped[str | None] = mapped_column(String(17), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
