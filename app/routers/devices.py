import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import redis

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User, UserDevice
from app.radius_database import get_radius_db
from app.redis_client import get_redis
from app.schemas.auth import MessageResponse
from app.schemas.devices import DeviceCreate, DevicePriority, DeviceUpdate
from app.schemas.me import DeviceOut
from app.services.account import device_out, device_plan
from app.services.coa_queue import enqueue_disconnect
from app.services.device_cache import cache_device_added, cache_device_removed
from app.services.devices import DeviceLimitReached, DeviceOwnedByAnotherUser, bind_device
from app.services.radius import remove_device_access
from app.utils.mac import InvalidMacError, normalise_mac
from app.utils.time import utc_now

router = APIRouter(prefix="/api/devices", tags=["devices"])
log = logging.getLogger(__name__)

DEVICE_NOT_FOUND = "Device not found."
DEVICE_LIMIT = "You have reached the device limit. Remove a device from your dashboard first."
DEVICE_OWNED = "This device is already linked to another account. Contact ICT."
UNAVAILABLE = "The login service is temporarily unavailable."


def _own_device(db: Session, user: User, device_id: int) -> UserDevice:
    """Other users' devices return 404, so ids cannot be probed."""
    device = db.scalar(
        select(UserDevice).where(UserDevice.id == device_id, UserDevice.user_id == user.id)
    )
    if device is None:
        raise HTTPException(status_code=404, detail=DEVICE_NOT_FOUND)
    return device


@router.post("", status_code=201, response_model=DeviceOut)
def add_device(
    body: DeviceCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
) -> DeviceOut:
    try:
        mac = normalise_mac(body.mac)
    except InvalidMacError:
        raise HTTPException(status_code=422, detail="Invalid device address.")

    now = utc_now()
    try:
        device = bind_device(db, user, mac, None, body.name, now)
    except DeviceOwnedByAnotherUser:
        db.rollback()
        raise HTTPException(status_code=409, detail=DEVICE_OWNED)
    except DeviceLimitReached:
        db.rollback()
        raise HTTPException(status_code=409, detail=DEVICE_LIMIT)

    db.commit()
    cache_device_added(r, user.email, mac)
    return device_out(device, user, device_plan(db, device), now)


@router.patch("/{device_id}", response_model=DeviceOut)
def update_device(
    device_id: int,
    body: DeviceUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DeviceOut:
    device = _own_device(db, user, device_id)
    if body.name is not None:
        device.device_name = body.name
    if body.auto_renew is not None:
        device.auto_renew = body.auto_renew
    db.commit()
    return device_out(device, user, device_plan(db, device), utc_now())


@router.put("/priority", response_model=MessageResponse)
def set_device_priority(
    body: DevicePriority,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Set the order plans link to this account's devices. Devices not listed lose their
    priority and come last."""
    devices = list(db.scalars(select(UserDevice).where(UserDevice.user_id == user.id)).all())
    own_ids = {d.id for d in devices}
    if len(set(body.device_ids)) != len(body.device_ids) or not set(body.device_ids) <= own_ids:
        raise HTTPException(
            status_code=422,
            detail="Each device must be listed once, and must belong to your account.",
        )
    order = {device_id: position for position, device_id in enumerate(body.device_ids, start=1)}
    for device in devices:
        device.priority = order.get(device.id)
    db.commit()
    return MessageResponse(ok=True, message="Device priority saved.")


@router.delete("/{device_id}", response_model=MessageResponse)
def delete_device(
    device_id: int,
    confirm: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    radius_db: Session = Depends(get_radius_db),
    r: redis.Redis = Depends(get_redis),
) -> MessageResponse:
    """Unbind a device from the account. The request must say confirm=true: the dashboard
    shows a warning first, because any time left on this device's plan is lost.

    Order matters: RADIUS first, then the database. If RADIUS fails, the device
    is kept and the user can retry. The reverse could leave access live for a
    device the user thinks they removed."""
    if not confirm:
        raise HTTPException(
            status_code=422,
            detail="Confirm that you want to remove this device. Any time left on its plan is lost.",
        )
    device = _own_device(db, user, device_id)
    now = utc_now()

    try:
        remove_device_access(radius_db, device.mac_address)
    except SQLAlchemyError:
        radius_db.rollback()
        raise HTTPException(status_code=503, detail=UNAVAILABLE)

    # End any live session through the CoA queue. The worker on the AAA VM sends the
    # disconnect and retries if the router does not answer. The removed device loses
    # access at its next login regardless.
    mac = device.mac_address
    enqueue_disconnect(db, "device_removed", now, mac=mac)
    db.delete(device)
    db.commit()
    cache_device_removed(r, user.email, mac)
    return MessageResponse(ok=True, message="Device removed.")
