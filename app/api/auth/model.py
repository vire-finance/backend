from sqlalchemy import String, Enum
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
        nullable=False
    )