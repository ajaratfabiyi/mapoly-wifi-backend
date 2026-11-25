import redis
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin
from app.models import BlockedMac, Plan, User, UserDevice
from app.radius_database import get_radius_db
from app.redis_client import get_redis
from app.schemas.admin_tools import BlockCreate, BlockOut, GrantPlan, OnlineSession
from app.schemas.common import UtcDatetime
from app.schemas.me import DeviceOut
from app.services.account import device_out, device_plan
from app.services.admin_accounts import (
    AdminActionError,
    block_mac,
    disconnect_account,
    disconnect_device,
    grant_plan_to_device,
    remove_device,
    unblock_mac,
)
from app.utils.mac import InvalidMacError, normalise_mac
from app.utils.time import utc_now

router = APIRouter(prefix="/api/admin", tags=["admin"])

DEVICE_NOT_FOUND = "Device not found."


class GrantOut(BaseModel):
    access_expires_at: UtcDatetime = None


def _device_or_404(db: Session, device_id: int) -> UserDevice:
    device = db.get(UserDevice, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=DEVICE_NOT_FOUND)
    return device


@router.get("/users/{user_id}/devices", response_model=list[DeviceOut])
def user_devices(
    user_id: int,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[DeviceOut]:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Account not found.")
    now = utc_now()
    devices = db.scalars(select(UserDevice).where(UserDevice.user_id == user.id)
                         .order_by(UserDevice.id)).all()
    return [device_out(d, user, device_plan(db, d), now) for d in devices]


@router.post("/users/{user_id}/disconnect")
def disconnect_account_devices(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    radius_db: Session = Depends(get_radius_db),
) -> dict:
    """Cut off every device on this account now (for example, a gated user). The account stays active."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Account not found.")
    count = disconnect_account(db, radius_db, admin, user, utc_now())
    return {"devices": count}


@router.post("/devices/{device_id}/disconnect", status_code=204)
def disconnect(
    device_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    radius_db: Session = Depends(get_radius_db),
) -> Response:
    """Cut the device off now. Its plan stays, so it can reconnect after logging in."""
    device = _device_or_404(db, device_id)
    disconnect_device(db, radius_db, admin, device, utc_now())
    return Response(status_code=204)


@router.delete("/devices/{device_id}", status_code=204)
def remove(
    device_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    radius_db: Session = Depends(get_radius_db),
    r: redis.Redis = Depends(get_redis),
) -> Response:
    device = _device_or_404(db, device_id)
    remove_device(db, radius_db, r, admin, device, utc_now())
    return Response(status_code=204)


@router.post("/devices/{device_id}/grant", response_model=GrantOut)
def grant_plan(
    device_id: int,
    body: GrantPlan,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    radius_db: Session = Depends(get_radius_db),
    r: redis.Redis = Depends(get_redis),
) -> GrantOut:
    """Give this device a plan for free, for example after a failed payment."""
    device = _device_or_404(db, device_id)
    plan = db.scalar(select(Plan).where(Plan.id == body.plan_id))
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found.")
    try:
        expires = grant_plan_to_device(db, radius_db, r, admin, device, plan, utc_now())
    except AdminActionError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    return GrantOut(access_expires_at=expires)


@router.get("/network/online", response_model=list[OnlineSession])
def online_now(
    _admin: User = Depends(require_admin),
    radius_db: Session = Depends(get_radius_db),
) -> list[OnlineSession]:
    """Devices with an open session on the router right now, from RADIUS accounting."""
    rows = radius_db.execute(text(
        "SELECT username, callingstationid, framedipaddress, acctstarttime "
        "FROM radacct WHERE acctstoptime IS NULL ORDER BY acctstarttime DESC LIMIT 500"
    )).all()
    return [OnlineSession(username=r[0], mac_address=r[1], ip_address=r[2], started_at=r[3])
            for r in rows]


@router.get("/blocks", response_model=list[BlockOut])
def list_blocks(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[BlockOut]:
    return [BlockOut(id=b.id, mac=b.mac, reason=b.reason, created_at=b.created_at)
            for b in db.scalars(select(BlockedMac).order_by(BlockedMac.id.desc())).all()]


@router.post("/blocks", response_model=BlockOut)
def add_block(
    body: BlockCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    radius_db: Session = Depends(get_radius_db),
) -> BlockOut:
    """Block a device. Blocked devices cannot sign in, even with a new account."""
    try:
        mac = normalise_mac(body.mac)
    except InvalidMacError:
        raise HTTPException(status_code=422, detail="Invalid device address.")
    try:
        block_mac(db, radius_db, admin, mac, body.reason.strip(), utc_now())
    except AdminActionError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    block = db.scalar(select(BlockedMac).where(BlockedMac.mac == mac))
    return BlockOut(id=block.id, mac=block.mac, reason=block.reason, created_at=block.created_at)


@router.delete("/blocks/{mac}", status_code=204)
def remove_block(
    mac: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    try:
        normalised = normalise_mac(mac)
    except InvalidMacError:
        raise HTTPException(status_code=422, detail="Invalid device address.")
    try:
        unblock_mac(db, admin, normalised, utc_now())
    except AdminActionError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    return Response(status_code=204)
