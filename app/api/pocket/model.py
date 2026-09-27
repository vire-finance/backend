import uuid

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import String, Numeric, ForeignKey, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base import Base, UUIDPrimaryKey, Timestamp

if TYPE_CHECKING:
    from app.api.card.model import Card


class Pocket(Base, UUIDPrimaryKey, Timestamp):
    __tablename__ = "pockets"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    allocated_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False
    )

    remaining_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False
    )

    theme: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    cards: Mapped[list["Card"]] = relationship(
        "Card",
        back_populates="pocket"
    )

    accesses: Mapped[list["PocketAccess"]] = relationship(
        "PocketAccess",
        back_populates="pocket",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "allocated_amount > 0",
            name="ck_pocket_allocated_amount_positive"
        ),
        CheckConstraint(
            "remaining_amount >= 0",
            name="ck_pocket_remaining_amount_non_negative"
        ),
        CheckConstraint(
            "remaining_amount <= allocated_amount",
            name="ck_pocket_remaining_not_exceed_allocated"
        ),
    )


class PocketAccess(Base, UUIDPrimaryKey, Timestamp):
    __tablename__ = "pocket_access"

    pocket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pockets.id"),
        nullable=False,
        index=True
    )

    employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True
    )

    pocket: Mapped["Pocket"] = relationship(
        "Pocket",
        back_populates="accesses"
    )

    __table_args__ = (
        UniqueConstraint(
            "pocket_id",
            "employee_id",
            name="uq_pocket_employee_access"
        ),
    )