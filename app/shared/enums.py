from enum import Enum

class UserRole(str, Enum):
    OWNER = "OWNER"
    EMPLOYEE = "EMPLOYEE"

class CardStatus(str, Enum):
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"
    NONACTIVE = "NONACTIVE"

class OCRStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    FROZEN = "FROZEN"
    NONACTIVE = "NONACTIVE"

class FundRequestStatus(str, Enum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"

class FundRequestType(str, Enum):
    CASH_ADVANCE = "CASH_ADVANCE"
    PURCHASE = "PURCHASE"
    OTHER = "OTHER"

class TransactionStatus(str, Enum):
    APPROVED = "APPROVED"
    DECLINED = "DECLINED"
    FAILED = "FAILED"

class TransactionType(str, Enum):
    FUND_REQUEST = "FUND_REQUEST"
    CARD_PAYMENT = "CARD_PAYMENT"
