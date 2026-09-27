import uuid

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.shared.enums import CardStatus

NonNegativeMoney = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]

class CardCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    last_four_digits: str = Field(min_length=4, max_length=4, pattern=r"^\d{4}$")
    network: str = Field(default="VISA", max_length=20)
    expiry_month: int = Field(ge=1, le=12)
    expiry_year: int
    balance: NonNegativeMoney = Decimal("0.00")
    theme: str | None = Field(default=None, max_length=50)


class CardUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    status: CardStatus | None = None
    theme: str | None = Field(default=None, max_length=50)
    balance: NonNegativeMoney | None = None

class CardAccessCreate(BaseModel):
    employee_id: uuid.UUID

class CardResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    pocket_id: uuid.UUID
    name: str
    last_four_digits: str
    network: str
    expiry_month: int
    expiry_year: int
    balance: Decimal
    spent: Decimal
    status: CardStatus
    theme: str | None

class CardAccessResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    card_id: uuid.UUID
    employee_id: uuid.UUID