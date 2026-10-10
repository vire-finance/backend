from typing import Literal
from uuid import UUID
from pydantic import Field
from app.shared.schema import Input, PositiveMoney

class FundAccountInput(Input):
    account_type: Literal["BANK", "E_WALLET"]
    provider_name: str = Field(min_length=1, max_length=100)
    account_number: str = Field(min_length=3, max_length=40, pattern=r"^[0-9 +()-]+$")
    account_holder: str = Field(min_length=1, max_length=150)

class AllocationInput(Input):
    pocket_id: UUID
    amount: PositiveMoney
