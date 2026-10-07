import uuid

from decimal import Decimal
from datetime import datetime

from sqlalchemy import Index, String, Numeric, ForeignKey, Enum, CheckConstraint, Text, DateTime, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base import Base, UUIDPrimaryKey, Timestamp
from app.shared.enums import TransactionType

class Transaction(Base, UUIDPrimaryKey, Timestamp):
    __tablename__ = "transactions"

    pocket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pockets.id"), nullable=False, index=True)
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cards.id"), nullable=False, index=True)
    fund_request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fund_requests.id"), nullable=True, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    transaction_type: Mapped[TransactionType] = mapped_column(Enum(TransactionType), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "amount > 0",
            name="ck_transaction_amount_positive"
        ),
        CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'DECLINED', 'FAILED')",
            name="ck_transaction_status",
        ),
        UniqueConstraint(
            "actor_id",
            "idempotency_key",
            name="uq_transaction_actor_key",
        ),
        Index(
            "uq_approved_transaction_per_request",
            "fund_request_id",
            unique=True,
            postgresql_where=text(
                "status = 'APPROVED' "
                "AND fund_request_id IS NOT NULL"
            ),
        ),
    )