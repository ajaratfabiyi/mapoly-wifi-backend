from pydantic import BaseModel

from app.schemas.common import UtcDatetime


class PaymentHistoryOut(BaseModel):
    """One line of a student's own payment history.

    `method` says how it was paid: card, transfer, ussd or voucher from Paystack, `points`
    for a points redemption, `admin` for a plan granted by an admin, or null if unknown.
    """

    reference: str
    plan_title: str
    amount_naira: int
    status: str
    method: str | None
    paid_at: UtcDatetime = None
    created_at: UtcDatetime = None
    refunded_at: UtcDatetime = None
