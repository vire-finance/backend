from uuid import UUID
from datetime import date
from typing import Literal, get_args

from pydantic import Field, field_validator

from app.api.transaction.schema import ExpenseCategory
from app.shared.enums import CardStatus
from app.shared.schema import Input, PositiveMoney, Name, NonNegativeMoney, Patch, Theme

CardUsageType = Literal["SINGLE_USE", "LONG_TERM", "SUBSCRIPTION"]

class CategoryPolicy:
    @field_validator("allowed_categories", check_fields=False)
    @classmethod
    def validate_categories(cls, value):
        if value is None or len(value) != len(set(value)):
            raise ValueError("Pilih kategori unik yang diizinkan untuk kartu.")
        return value

class CardCreate(CategoryPolicy, Input):
    allowed_categories: list[ExpenseCategory] = Field(default_factory=lambda: list(get_args(ExpenseCategory)), min_length=1, max_length=7)
    expires_on: date | None = None
    usage_type: CardUsageType = "LONG_TERM"
    pocket_id: UUID
    name: Name
    theme: Theme
    initial_balance: NonNegativeMoney
    monthly_limit: PositiveMoney
    category: str | None = Field(None, min_length=1, max_length=100)

class CardUpdate(CategoryPolicy, Patch):
    allowed_categories: list[ExpenseCategory] | None = Field(None, min_length=1, max_length=7)
    blocked: bool | None = None
    expires_on: date | None = None
    usage_type: CardUsageType | None = None
    name: Name | None = None
    theme: Theme | None = None
    balance: NonNegativeMoney | None = None
    monthly_limit: PositiveMoney | None = None
    category: str | None = Field(None, min_length=1, max_length=100)

class CardStatusUpdate(Input):
    status: CardStatus