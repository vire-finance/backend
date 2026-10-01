import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base import Base, Timestamp, UUIDPrimaryKey
from app.shared.enums import CardStatus


class Card(Base, UUIDPrimaryKey, Timestamp):
    __tablename__ = "cards"

    pocket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pockets.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    last_four_digits: Mapped[str] = mapped_column(
        String(4), nullable=False
    )
    network: Mapped[str] = mapped_column(
        String(20), nullable=False, default="VISA"
    )
    expiry_month: Mapped[int] = mapped_column(nullable=False)
    expiry_year: Mapped[int] = mapped_column(nullable=False)
    balance: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    spent: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    monthly_limit: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False
    )
    status: Mapped[CardStatus] = mapped_column(
        Enum(CardStatus), nullable=False, default=CardStatus.ACTIVE
    )
    theme: Mapped[str | None] = mapped_column(String(50), nullable=True)

    pocket = relationship("Pocket", back_populates="cards")
    accesses = relationship("CardAccess", back_populates="card", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("balance >= 0", name="ck_card_balance_non_negative"),
        CheckConstraint("spent >= 0", name="ck_card_spent_non_negative"),
        CheckConstraint(
            "expiry_month >= 1 AND expiry_month <= 12",
            name="ck_card_expiry_month",
        ),
        CheckConstraint(
            "monthly_limit > 0", name="ck_card_monthly_limit_positive"
        ),
    )

class CardAccess(Base, UUIDPrimaryKey, Timestamp):
    __tablename__ = "card_access"

    card_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cards.id"), nullable=False, index=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    card = relationship("Card", back_populates="accesses")

    __table_args__ = (
        UniqueConstraint(
            "card_id", "employee_id",
            name="uq_card_employee_access",
        ),
    )