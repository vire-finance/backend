from uuid import UUID

from pydantic import BaseModel

class NotificationResponse(BaseModel):
    id: UUID
    request_id: UUID | None
    title: str
    message: str
    is_read: bool
    created_at: str