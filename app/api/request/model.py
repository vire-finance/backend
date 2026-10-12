import uuid

from decimal import Decimal
from datetime import datetime

from sqlalchemy import String, Numeric, ForeignKey, Enum, Text, DateTime, Boolean, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship

from app.shared.base import Base, UUIDPrimaryKey, Timestamp
from app.shared.enums import FundRequestStatus, FundRequestType

class FundRequest(Base, UUIDPrimaryKey, Timestamp):
    __tablename__ = "fund_requests"
    requester_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    pocket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pockets.id"), nullable=False, index=True)
    payment_executor: Mapped[str] = mapped_column(String(30), nullable=False, default="OWNER_PAYMENT", server_default="OWNER_PAYMENT")
    card_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cards.id"), nullable=True)
    payment_method: Mapped[str] = mapped_column(String(30), nullable=False, default="QRIS", server_default="QRIS")
    recipient_account: Mapped[str | None] = mapped_column(String(150), nullable=True)
    receipt_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    receipt_due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    receipt_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ocr_documents.id", use_alter=True), nullable=True)
    receipt_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    receipt_review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    receipt_overdue_notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    request_type: Mapped[FundRequestType] = mapped_column(Enum(FundRequestType), nullable=False, default=FundRequestType.PURCHASE, index=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="Others", server_default="Others", index=True)
    party_name: Mapped[str] = mapped_column(String(150), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    needed_by: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[FundRequestStatus] = mapped_column(Enum(FundRequestStatus), nullable=False, default=FundRequestStatus.DRAFT, index=True)
    ai_analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    documents: Mapped[list["FundRequestDocument"]] = relationship(
        "FundRequestDocument",
        back_populates="fund_request",
        cascade="all, delete-orphan"
    )
    __table_args__ = (
        CheckConstraint(
            "total_amount > 0",
            name="ck_fund_request_amount_positive"
        ),
    )

class FundRequestDocument(Base, UUIDPrimaryKey, Timestamp):
    __tablename__ = "fund_request_documents"
    fund_request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fund_requests.id"), nullable=False, index=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_url: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fund_request: Mapped["FundRequest"] = relationship("FundRequest", back_populates="documents")


class RequestEvent(Base, UUIDPrimaryKey, Timestamp):
    """Append-only review conversation. Existing requests need no backfill."""
    __tablename__ = "request_events"
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fund_requests.id"), index=True)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    event_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40))
    message: Mapped[str | None] = mapped_column(Text)
    action_key: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    __table_args__ = (UniqueConstraint("request_id", "action_key", name="uq_request_event_key"),)
