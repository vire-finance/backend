from uuid import UUID
from typing import Literal

from pydantic import Field

from app.shared.schema import Input, PositiveMoney

ExpenseCategory = Literal["Salary", "Operational", "Production", "Marketing", "Emergency", "Administration & Tax", "Others"]

class TransactionCreate(Input):
    category: ExpenseCategory = "Others"
    request_id: UUID | None = None
    payment_method: Literal["QRIS", "BANK_TRANSFER", "E_WALLET"] = "QRIS"
    recipient_account: str | None = Field(None, max_length=150)
    card_id: UUID
    amount: PositiveMoney
    description: str = Field(min_length=1, max_length=255)