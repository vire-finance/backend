from enum import Enum

class UserRole(str, Enum):
    OWNER = "OWNER"
    EMPLOYEE = "EMPLOYEE"

class CardStatus(str, Enum):
    ACTIVE = "ACTIVE"
    NONACTIVE = "NONACTIVE"