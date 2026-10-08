from uuid import UUID

from pydantic import Field

from app.shared.schema import Input, PositiveMoney

class TransactionCreate(Input):
    card_id: UUID
    amount: PositiveMoney
    description: str = Field(min_length=1, max_length=255)