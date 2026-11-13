from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LoginToken(Base):
    """Tracks every router login token so the cleanup job can remove unused ones.

    radcheck has no expiry, so without this table an unused token stays valid at
    RADIUS indefinitely. FreeRADIUS deletes the radcheck row when a token is used;
    this row stays until cleanup removes it.
    """

    __tablename__ = "login_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(64), unique=True)
    username: Mapped[str] = mapped_column(String(255), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
