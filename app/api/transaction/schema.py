from uuid import UUID
from typing import Literal

from pydantic import Field, model_validator

from app.shared.schema import Input, PositiveMoney
from app.shared.payment_destination import PaymentDestination

ExpenseCategory = Literal["Salary", "Operational", "Production", "Marketing", "Emergency", "Administration & Tax", "Others"]

class TransactionCreate(Input):
    category: ExpenseCategory = "Others"
    request_id: UUID | None = None
    payment_method: Literal["QRIS", "BANK_TRANSFER", "E_WALLET"] = "QRIS"
    recipient_account: str | None = Field(None, max_length=150)
    payment_type: Literal["BANK", "E_WALLET"] | None = None
    destination_name: str | None = Field(None, min_length=1, max_length=100)
    destination_account_number: str | None = Field(None, min_length=1, max_length=150, pattern=r"^[0-9]+$", strict=True)
    destination_bank_name: str | None = Field(None, min_length=1, max_length=100)
    card_id: UUID
    amount: PositiveMoney
    description: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def validate_destination(self):
        destination = self.destination
        if destination is None:
            return self  # Existing clients and invoice/QRIS flows stay compatible.
        if "payment_method" in self.model_fields_set and self.payment_method != destination.payment_method:
            raise ValueError("payment_method conflicts with payment_type.")
        if "recipient_account" in self.model_fields_set and self.recipient_account != destination.recipient_account:
            raise ValueError("recipient_account conflicts with structured destination.")
        self.payment_method = destination.payment_method
        self.recipient_account = destination.recipient_account
        return self

    @property
    def destination(self) -> PaymentDestination | None:
        names = PaymentDestination.model_fields
        if not self.model_fields_set.intersection(names):
            return None
        return PaymentDestination.model_validate({name: getattr(self, name) for name in names})
