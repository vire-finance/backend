import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class LinkedFundAccount(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    allocated_amount: Decimal
    remaining_amount: Decimal
    theme: str | None


class ProfileResponse(BaseModel):
    id: uuid.UUID
    avatar_url: str | None
    full_name: str
    role: str
    linked_fund_accounts: list[LinkedFundAccount]


class EmployeeResponse(BaseModel):
    id: uuid.UUID
    avatar_url: str | None
    full_name: str
    role: str
