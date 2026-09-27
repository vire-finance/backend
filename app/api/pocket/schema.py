import uuid

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.api.card.schema import CardResponse

NonNegativeMoney = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]

class PocketCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    allocated_amount: NonNegativeMoney
    theme: str | None = Field(default=None, max_length=50)

class PocketUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    theme: str | None = Field(default=None, max_length=50)

class PocketBudgetUpdate(BaseModel):
    allocated_amount: NonNegativeMoney

class PocketAccessCreate(BaseModel):
    employee_id: uuid.UUID

class PocketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    allocated_amount: Decimal
    remaining_amount: Decimal
    theme: str | None

class PocketListItem(PocketResponse):
    card_count: int

class PocketDetail(PocketResponse):
    cards: list[CardResponse]

class PocketAccessResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    pocket_id: uuid.UUID
    employee_id: uuid.UUID