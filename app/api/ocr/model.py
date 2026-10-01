import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Date, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base import Base, Timestamp, UUIDPrimaryKey
from app.shared.enums import OCRStatus

if TYPE_CHECKING:
    from app.api.auth.model import User


class OCRDocument(Base, UUIDPrimaryKey, Timestamp):
    __tablename__ = "ocr_documents"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    fund_request_id: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True,
        index=True,
    )

    original_filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    stored_filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
    )

    storage_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    mime_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    file_size: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    ocr_status: Mapped[OCRStatus] = mapped_column(
        Enum(
            OCRStatus,
            values_callable=lambda x: [item.value for item in x],
            name="ocrstatus",
        ),
        nullable=False,
        default=OCRStatus.PENDING,
        index=True,
    )

    raw_ocr_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    extracted_other_party_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    extracted_total_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2),
        nullable=True,
    )

    extracted_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    ocr_error: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    user: Mapped["User"] = relationship(
        "User",
        foreign_keys=[user_id],
    )