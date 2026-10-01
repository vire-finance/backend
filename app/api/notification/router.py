from uuid import UUID

from fastapi import APIRouter, Query

from app.api.notification.service import NotificationService
from app.core.context import Ctx

router = APIRouter(prefix="/notifications", tags=["Notification"])

@router.get("")
def list_notifications(ctx: Ctx, unread_only: bool = Query(False), offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    return NotificationService(ctx).list(unread_only, offset, limit)

@router.patch("/{notification_id}/read")
def read_notification(notification_id: UUID, ctx: Ctx):
    return NotificationService(ctx).read(notification_id)