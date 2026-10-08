from app.api.notification.model import Notification
from app.api.notification.repository import NotificationRepository
from app.shared.utils import ensure, fields

class NotificationService:
    def __init__(self, ctx):
        self.ctx = ctx
        self.repo = NotificationRepository(ctx.db)

    def enqueue(self, user_id, request_id, title, message):
        self.ctx.db.add(
            Notification(
                user_id=user_id,
                request_id=request_id,
                title=title,
                message=message,
            )
        )

    def list(self, unread_only=False, offset=0, limit=50):
        rows = self.repo.for_user(self.ctx.user.id)
        unread_count = sum(not row.is_read for row in rows)

        if unread_only:
            rows = [row for row in rows if not row.is_read]

        return {
            "unread_count": unread_count,
            "total": len(rows),
            "items": [
                fields(
                    row, "id", "request_id", "title", "message", "is_read", "created_at",
                )
                for row in rows[offset:offset + limit]
            ],
        }

    def read(self, notification_id):
        row = self.repo.get(notification_id)

        ensure(
            row is not None and row.user_id == self.ctx.user.id,
            "Notifikasi tidak ditemukan.",
            404,
        )

        row.is_read = True
        return self.ctx.commit({"message": "Notifikasi dibaca."})

    def unread_count(self):
        from sqlalchemy import func, select
        count = self.ctx.db.scalar(select(func.count(Notification.id)).where(
            Notification.user_id == self.ctx.user.id, Notification.is_read.is_(False))) or 0
        return {"unread_count": count}

    def read_all(self):
        from sqlalchemy import update
        result = self.ctx.db.execute(update(Notification).where(
            Notification.user_id == self.ctx.user.id, Notification.is_read.is_(False)).values(is_read=True))
        return self.ctx.commit({"updated_count": result.rowcount, "unread_count": 0})
