from datetime import datetime, timezone
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator

from app.shared.enums import FundRequestType
from app.shared.schema import Input, Money

class FundRequestInput(Input):
    pocket_id: UUID
    explanation: str = Field(min_length=1, max_length=5000)
    request_type: FundRequestType
    party_name: str = Field(min_length=1, max_length=150)
    total_amount: Money
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