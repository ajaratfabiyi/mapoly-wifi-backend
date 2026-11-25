"""Import every model here so Alembic and create_all see the full metadata."""

from app.models.base import Base
from app.models.coa_queue import CoaQueue
from app.models.device import UserDevice
from app.models.ledger import Ledger
from app.models.login_token import LoginToken
from app.models.plan import Plan
from app.models.admin_action import AdminAction
from app.models.announcement import Announcement
from app.models.block import BlockedMac
from app.models.points_entry import PointsEntry
from app.models.session_log import SessionLog
from app.models.user import User

__all__ = [
    "Base", "User", "UserDevice", "Plan", "Ledger", "PointsEntry", "AdminAction",
    "BlockedMac", "Announcement",
    "SessionLog", "LoginToken", "CoaQueue",
]
