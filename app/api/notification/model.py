import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String, Text, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base import Base, Timestamp, UUIDPrimaryKey

class Notification(Base, UUIDPrimaryKey, Timestamp):
    __tablename__ = "finance_notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fund_requests.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    push_processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default="false")
    push_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    push_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
class PushDevice(Base, UUIDPrimaryKey, Timestamp):
    __tablename__ = "push_devices"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
