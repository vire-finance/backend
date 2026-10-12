from uuid import UUID

from fastapi import APIRouter, Query, Depends
from fastapi.security import HTTPAuthorizationCredentials
from app.api.auth.dependencies import bearer_scheme
from app.api.auth.security import decode_access_token
from app.api.auth.model import AuthSession
from app.shared.utils import ensure

from app.api.notification.service import NotificationService
from app.core.context import Ctx
from app.shared.schema import Input
from pydantic import Field
from sqlalchemy import select
from app.api.notification.model import PushDevice

router = APIRouter(prefix="/notifications", tags=["Notification"])

@router.get("")
def list_notifications(ctx: Ctx, unread_only: bool = Query(False), offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    return NotificationService(ctx).list(unread_only, offset, limit)

@router.patch("/{notification_id}/read")
def read_notification(notification_id: UUID, ctx: Ctx):
    return NotificationService(ctx).read(notification_id)

@router.get("/unread-count")
def unread_count(ctx: Ctx):
    return NotificationService(ctx).unread_count()


@router.patch("/read-all")
def read_all(ctx: Ctx):
    return NotificationService(ctx).read_all()

class DeviceInput(Input):
    token: str = Field(min_length=20, max_length=500)

@router.post("/devices")
def register_device(payload: DeviceInput, ctx: Ctx, credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    session_id = UUID(decode_access_token(credentials.credentials)["sid"])
    session = ctx.db.scalar(select(AuthSession).where(AuthSession.id == session_id).with_for_update().execution_options(populate_existing=True))
    ensure(session is not None and session.user_id == ctx.user.id and session.revoked_at is None,
           "Authentication required.", 401)
    from sqlalchemy.dialects.postgresql import insert
    statement = insert(PushDevice).values(user_id=ctx.user.id, token=payload.token, session_id=session_id)
    ctx.db.execute(statement.on_conflict_do_update(index_elements=[PushDevice.token],
        set_={"user_id": ctx.user.id, "session_id": session_id}))
    return ctx.commit({"registered": True})

@router.delete("/devices")
def remove_device(payload: DeviceInput, ctx: Ctx):
    device = ctx.db.scalar(select(PushDevice).where(PushDevice.token == payload.token, PushDevice.user_id == ctx.user.id))
    if device: ctx.db.delete(device)
    return ctx.commit({"removed": True})
