from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


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


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=10, max_length=128)
    confirm_password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_strength(cls, value):
        from app.api.auth.schema import RegisterRequest
        return RegisterRequest.validate_password_strength(value)

    @model_validator(mode="after")
    def passwords_match(self):
        if self.new_password.get_secret_value() != self.confirm_password.get_secret_value():
            raise ValueError("Password confirmation does not match")
        if self.current_password.get_secret_value() == self.new_password.get_secret_value():
            raise ValueError("New password must differ from the current password")
        return self
