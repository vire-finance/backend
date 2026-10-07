from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator, model_validator
from typing import Literal


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    phone_number: str = Field(min_length=8, max_length=30, pattern=r"^[+0-9 ()-]+$")
    password: SecretStr = Field(min_length=10, max_length=128)
    confirm_password: SecretStr

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        if not 8 <= sum(char.isdigit() for char in value) <= 15:
            raise ValueError("Phone number must contain 8 to 15 digits")
        return value.strip()

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: SecretStr) -> SecretStr:
        password = value.get_secret_value()
        if not any(char.islower() for char in password) or not any(char.isupper() for char in password) or not any(char.isdigit() for char in password):
            raise ValueError("Password must include uppercase, lowercase, and numeric characters")
        return value

    @model_validator(mode="after")
    def passwords_match(self):
        if self.password.get_secret_value() != self.confirm_password.get_secret_value():
            raise ValueError("Password confirmation does not match")
        return self


class OwnerSetupRequest(BaseModel):
    fund_account_type: Literal["BANK", "E_WALLET"]
    employees_managed: int = Field(ge=1, le=500)


class EmployeeSetupRequest(BaseModel):
    invitation_code: str = Field(min_length=12, max_length=64)


class LoginRequest(BaseModel):
    email: EmailStr
    password: SecretStr


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str


class RegistrationResponse(BaseModel):
    user: "UserResponse"
    setup_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    id: str
    email: EmailStr
    username: str
    phone_number: str
    role: str | None


class InviteResponse(BaseModel):
    code: str
    expires_at: str
