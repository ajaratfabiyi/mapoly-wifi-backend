from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin
from app.models import Announcement, User
from app.schemas.admin_tools import AnnouncementCreate, AnnouncementOut, AnnouncementPatch, PublicAnnouncement
from app.services.admin_log import record_admin_action
from app.utils.time import utc_now

public = APIRouter(prefix="/api", tags=["announcements"])
admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


def _out(a: Announcement) -> AnnouncementOut:
    return AnnouncementOut(id=a.id, message=a.message, active=bool(a.active), created_at=a.created_at)


@public.get("/announcements", response_model=list[PublicAnnouncement])
def active_announcements(db: Session = Depends(get_db)) -> list[PublicAnnouncement]:
    """Active messages for the portal, newest first. Public, so it works before sign-in."""
    rows = db.scalars(select(Announcement).where(Announcement.active.is_(True))
                      .order_by(desc(Announcement.id)).limit(5)).all()
    return [PublicAnnouncement(id=a.id, message=a.message, created_at=a.created_at) for a in rows]


@admin_router.get("/announcements", response_model=list[AnnouncementOut])
def all_announcements(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[AnnouncementOut]:
    return [_out(a) for a in db.scalars(select(Announcement).order_by(desc(Announcement.id))).all()]


@admin_router.post("/announcements", response_model=AnnouncementOut)
def create_announcement(
    body: AnnouncementCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AnnouncementOut:
    now = utc_now()
    row = Announcement(message=body.message.strip(), active=True, created_at=now)
    db.add(row)
    db.flush()
    record_admin_action(db, admin, "create_announcement", None, row.message[:200], now)
    db.commit()
    return _out(row)


@admin_router.patch("/announcements/{announcement_id}", response_model=AnnouncementOut)
def set_announcement_active(
    announcement_id: int,
    body: AnnouncementPatch,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AnnouncementOut:
    row = db.get(Announcement, announcement_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Announcement not found.")
    row.active = body.active
    record_admin_action(db, admin, "set_announcement_active", None,
                        f"#{row.id} active={body.active}", utc_now())
    db.commit()
    return _out(row)
