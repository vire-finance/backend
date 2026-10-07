from typing import Literal
import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


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


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    full_name: str | None = Field(None, min_length=1, max_length=200)
    avatar_url: HttpUrl | None = Field(None, max_length=2048)


class WorkspaceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employees_managed: int | None = Field(None, ge=1, le=500, strict=True)
    fund_account_type: Literal["BANK", "E_WALLET"] | None = None

    @field_validator("employees_managed", "fund_account_type")
    @classmethod
    def reject_null(cls, value):
        if value is None:
            raise ValueError("Workspace values cannot be null")
        return value
