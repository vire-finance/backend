from enum import Enum


class UserRole(str, Enum):
    OWNER = "OWNER"
    EMPLOYEE = "EMPLOYEE"


class CardStatus(str, Enum):
    ACTIVE = "ACTIVE"
    NONACTIVE = "NONACTIVE"


class OCRStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"