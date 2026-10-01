import uuid

from datetime import datetime
from sqlalchemy import String, Enum, ForeignKey, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base import Base, UUIDPrimaryKey, Timestamp
from app.shared.enums import UserRole


class User(Base, UUIDPrimaryKey, Timestamp):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False
    )

    username: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    phone_number: Mapped[str] = mapped_column(
        String(30),
        nullable=False
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole),
        nullable=True
    )

    employer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    fund_account_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    employees_managed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    invite_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    invite_code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthSession(Base, UUIDPrimaryKey):
    __tablename__ = "auth_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
