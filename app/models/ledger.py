from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Ledger(Base):
    """Section 3.4. One row per payment attempt. `reference` is the Paystack reference
    and its UNIQUE constraint blocks double-crediting."""

    __tablename__ = "ledger"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    reference: Mapped[str] = mapped_column(String(100), unique=True)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_devices.id", ondelete="SET NULL"), nullable=True
    )
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"))

    amount_kobo: Mapped[int] = mapped_column(BigInteger)
    # Nullable: the method is unknown until Paystack reports it after initialize.
    payment_method: Mapped[str | None] = mapped_column(
        Enum("card", "transfer", "ussd", "voucher", name="payment_method"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        Enum("pending", "success", "failed", name="ledger_status"),
        default="pending",
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Set by an admin once the money has been sent back. Recorded only; the refund itself is done at Paystack.
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="ledger_entries")


from app.models.user import User  # noqa: E402
