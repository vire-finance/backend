from datetime import datetime
import uuid

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, JSON, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base import Base, Timestamp, UUIDPrimaryKey
from app.shared.enums import UserRole

notification_json = JSON().with_variant(JSONB, "postgresql")


class User(Base, UUIDPrimaryKey, Timestamp):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )

    username: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    phone_number: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    role: Mapped[UserRole | None] = mapped_column(
        Enum(UserRole),
        nullable=True,
    )

    full_name: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    avatar_url: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )

    # Business Owner / Employer Fields
    business_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    employer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    fund_account_type: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    employees_managed: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    invite_code_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        unique=True,
    )

    invite_code_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Security & PIN Verification Fields (Copilot)
    security_pin: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        deferred=True,
    )

    pin_failed_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    security_pin_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
    )

    pin_locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    biometric_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    notification_preferences: Mapped[dict[str, bool]] = mapped_column(
        notification_json,
        nullable=False,
        default=dict,
        server_default="{}",
    )


class AuthSession(Base, UUIDPrimaryKey):
    __tablename__ = "auth_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
