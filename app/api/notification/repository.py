from sqlalchemy import select

from app.api.notification.model import Notification
from app.shared.repository import Repository

class NotificationRepository(Repository):
    model = Notification

    def for_user(self, user_id):
        return self.db.scalars(
            select(Notification)
            .where(Notification.user_id == user_id)
            .order_by(Notification.created_at.desc(), Notification.id)
        ).all()