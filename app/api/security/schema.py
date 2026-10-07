from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChangePinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    old_pin: str | None = Field(default=None, pattern=r"^[0-9]{4,12}$")
    new_pin: str = Field(pattern=r"^[0-9]{4,12}$")


class BiometricToggleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class NotificationPreferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: bool | None = None
    push: bool | None = None
    sms: bool | None = None


class VerifyPinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pin: str = Field(pattern=r"^[0-9]{4,12}$")
    action_type: Literal["PAYMENT", "APPROVAL"]


class VerifyPinResponse(BaseModel):
    action_token: str
    expires_in: int
