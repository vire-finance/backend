from uuid import UUID

from pydantic import Field

from app.shared.enums import CardStatus
from app.shared.schema import Input, Money, Name, NonNegativeMoney, Patch, Theme

class CardCreate(Input):
    pocket_id: UUID
    name: Name
    theme: Theme
    initial_balance: NonNegativeMoney
    monthly_limit: Money
    category: str | None = Field(None, min_length=1, max_length=100)

class CardUpdate(Patch):
    name: Name | None = None
    theme: Theme | None = None
    balance: NonNegativeMoney | None = None
    monthly_limit: Money | None = None
    category: str | None = Field(None, min_length=1, max_length=100)

class CardStatusUpdate(Input):
    status: CardStatus