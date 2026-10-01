from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_MONEY = 1_000_000_000_000_000

Money = Annotated[int, Field(strict=True, ge=0, le=MAX_MONEY)]
NonNegativeMoney = Annotated[int, Field(ge=0, le=MAX_MONEY)]
Name = Annotated[str, Field(min_length=1, max_length=100)]

Theme = Literal["BLUE", "PURPLE", "GREEN", "ORANGE", "RED", "PINK", "BLACK"]

class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

class Patch(Input):
    @model_validator(mode="after")
    def reject_explicit_null(self):
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} tidak boleh null.")
        return self

class Confirmation(Input):
    confirmed: Literal[True]