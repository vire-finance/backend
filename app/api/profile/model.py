from uuid import UUID
from sqlalchemy import ForeignKey, String, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.shared.base import Base, Timestamp, UUIDPrimaryKey


class ProfilePhoto(Base, UUIDPrimaryKey, Timestamp):
    __tablename__ = 'profile_photos'
    user_id: Mapped[UUID] = mapped_column(ForeignKey('users.id'), unique=True)
    storage_path: Mapped[str] = mapped_column(String(1024))
    stored_filename: Mapped[str] = mapped_column(String(100))
    file_size: Mapped[int] = mapped_column(Integer)
    mime_type: Mapped[str] = mapped_column(String(50))
    original_filename: Mapped[str] = mapped_column(String(100))
