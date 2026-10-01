from uuid import UUID

from pydantic import Field

from app.shared.schema import Input, Money

class TransactionCreate(Input):
    card_id: UUID
    amount: Money
    description: str = Field(min_length=1, max_length=255)