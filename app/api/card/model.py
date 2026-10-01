import uuid

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import String, Numeric, ForeignKey, Enum, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base import Base, UUIDPrimaryKey, Timestamp
from app.shared.enums import CardStatus

if TYPE_CHECKING:
    from app.api.pocket.model import Pocket

class Card(Base, UUIDPrimaryKey, Timestamp):
    __tablename__ = "cards"

    pocket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pockets.id"),
        nullable=False,
        index=True
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    last_four_digits: Mapped[str] = mapped_column(
        String(4),
        nullable=False
    )

    network: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="VISA"
    )

    expiry_month: Mapped[int] = mapped_column(
        nullable=False
    )

    expiry_year: Mapped[int] = mapped_column(
        nullable=False
    )

    balance: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0.00")
    )

    spent: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0.00")
    )

    status: Mapped[CardStatus] = mapped_column(
        Enum(CardStatus),
        nullable=False,
        default=CardStatus.ACTIVE
    )

    theme: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    pocket: Mapped["Pocket"] = relationship(
        "Pocket",
        back_populates="cards"
    )

    accesses: Mapped[list["CardAccess"]] = relationship(
        "CardAccess",
        back_populates="card",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "balance >= 0",
            name="ck_card_balance_non_negative"
        ),
        CheckConstraint(
            "spent >= 0",
            name="ck_card_spent_non_negative"
        ),
        CheckConstraint(
            "expiry_month >= 1 AND expiry_month <= 12",
            name="ck_card_expiry_month"
        ),
    )


class CardAccess(Base, UUIDPrimaryKey, Timestamp):
    __tablename__ = "card_access"

    card_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cards.id"),
        nullable=False,
        index=True
    )

    employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True
    )

    card: Mapped["Card"] = relationship(
        "Card",
        back_populates="accesses"
    )

    __table_args__ = (
        UniqueConstraint(
            "card_id",
            "employee_id",
            name="uq_card_employee_access"
        ),
    )