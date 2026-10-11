"""Validated destination metadata for direct simulated payments."""
from typing import Literal

from pydantic import Field, model_validator

from app.shared.schema import Input

BANKS = ("BCA", "BRI", "BNI", "Mandiri", "BTN", "CIMB Niaga", "PermataBank",
         "Bank Syariah Indonesia (BSI)", "Danamon", "OCBC", "Other")
E_WALLETS = ("DANA", "GoPay", "OVO", "ShopeePay", "LinkAja")


class PaymentDestination(Input):
    payment_type: Literal["BANK", "E_WALLET"]
    destination_name: str = Field(min_length=1, max_length=100)
    destination_account_number: str = Field(min_length=1, pattern=r"^[0-9]+$", strict=True)
    destination_bank_name: str | None = Field(None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def valid_destination(self):
        options = BANKS if self.payment_type == "BANK" else E_WALLETS
        if self.destination_name not in options:
            raise ValueError("Select a valid destination provider.")
        other = self.payment_type == "BANK" and self.destination_name == "Other"
        if other != (self.destination_bank_name is not None):
            raise ValueError("Bank Name is required only when Other bank is selected.")
        if self.destination_bank_name and (not any(c.isalpha() for c in self.destination_bank_name)
                                           or any(ord(c) < 32 for c in self.destination_bank_name)):
            raise ValueError("Enter a valid bank name.")
        # Match the existing recipient column capacity, rather than prescribing
        # a fixed account-number length. Numbers always remain strings.
        if len(self.recipient_account) > 150:
            raise ValueError("Destination exceeds the existing 150-character recipient capacity.")
        return self

    @property
    def payment_method(self):
        return "BANK_TRANSFER" if self.payment_type == "BANK" else "E_WALLET"

    @property
    def recipient_account(self):
        return f"{self.destination_bank_name or self.destination_name} {self.destination_account_number}"
