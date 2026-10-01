import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, UniqueConstraint

from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base import Base, Timestamp, UUIDPrimaryKey


class TopUp(Base, UUIDPrimaryKey, Timestamp):
    __tablename__ = "simulated_topups"

    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    pocket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pockets.id"), nullable=False, index=True)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "owner_id", "idempotency_key",
            name="uq_topup_owner_key",
        ),
        CheckConstraint("amount > 0", name="ck_topup_amount_positive"),
        CheckConstraint(
            "method IN ('QR_CODE', 'BANK_TRANSFER')",
            name="ck_topup_method",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'COMPLETED')",
            name="ck_topup_status",
        ),
    )


class InvoicePayment(Base, UUIDPrimaryKey, Timestamp):
    __tablename__ = "manual_invoice_payments"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transactions.id"), nullable=False, unique=True
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fund_requests.id"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="QUEUED"
    )
    simulated_paid_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED', 'SIMULATED_PAID')",
            name="ck_invoice_payment_status",
        ),
    )