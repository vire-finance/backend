from datetime import datetime, timezone
from uuid import UUID
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator

from app.shared.enums import FundRequestType
from app.api.transaction.schema import ExpenseCategory
from app.shared.schema import Input, PositiveMoney

RequestCategory = Literal["Operational", "Production", "Marketing", "Emergency", "Administration & Tax", "Others"]

class FundRequestInput(Input):
    pocket_id: UUID
    explanation: str = Field(min_length=1, max_length=5000)
    category: ExpenseCategory = "Others"
    payment_executor: Literal["OWNER_PAYMENT", "EMPLOYEE_PAYMENT"] = "OWNER_PAYMENT"
    card_id: UUID | None = None
    payment_method: Literal["QRIS", "BANK_TRANSFER", "E_WALLET"] = "QRIS"
    recipient_account: str | None = Field(None, min_length=1, max_length=150)
    request_type: FundRequestType
    party_name: str = Field(min_length=1, max_length=150)
    total_amount: PositiveMoney
    needed_by: AwareDatetime

    @field_validator("needed_by")
    @classmethod
    def validate_deadline(cls, value):
        if value <= datetime.now(timezone.utc):
            raise ValueError("needed_by harus berada di masa depan.")
        return value

class DocumentAttach(Input):
    document_id: UUID

class RequestSubmit(Input):
    allow_failed_ocr: bool = Field(default=False, strict=True)

class RequestApprove(Input):
    card_id: UUID

class RequestReject(Input):
    reason: str = Field(min_length=1, max_length=1000)
class ReceiptSubmit(Input):
    document_id: UUID
    note: str = Field(default="", max_length=5000)

class ReceiptReview(Input):
    decision: Literal["VERIFIED", "NEEDS_CLARIFICATION"]
    reason: str = Field(default="", max_length=1000)
