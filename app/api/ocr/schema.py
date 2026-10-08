import datetime
import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.shared.enums import OCRStatus


class ExtractedData(BaseModel):
    other_party_name: str | None = Field(default=None)
    total_amount: Decimal | None = Field(default=None)
    date: datetime.date | None = Field(default=None)
    explanation: str | None = Field(default=None)


class OCRDocumentUploadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: uuid.UUID
    status: OCRStatus


class OCRDocumentDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: uuid.UUID
    status: OCRStatus
    extracted_data: ExtractedData | None = Field(default=None)
    message: str | None = Field(default=None)
