import uuid
from decimal import Decimal
from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.shared.base import Base, UUIDPrimaryKey, Timestamp

class FundAccount(Base, UUIDPrimaryKey, Timestamp):
    __tablename__ = "main_fund_accounts"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    account_type: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(100), nullable=False)
    account_number: Mapped[str] = mapped_column(String(40), nullable=False)
    account_holder: Mapped[str] = mapped_column(String(150), nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=20_000_000, nullable=False)
    __table_args__ = (CheckConstraint("balance >= 0 AND balance <= 1000000000000000", name="ck_main_fund_balance"), CheckConstraint("account_type IN ('BANK', 'E_WALLET')", name="ck_main_fund_type"))

class FundMovement(Base, UUIDPrimaryKey, Timestamp):
    __tablename__ = "main_fund_movements"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("main_fund_accounts.id"), nullable=False)
    pocket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pockets.id"), nullable=True)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    __table_args__ = (UniqueConstraint("owner_id", "idempotency_key", name="uq_main_fund_movement_key"), CheckConstraint("amount > 0", name="ck_main_fund_movement_amount"), CheckConstraint("kind IN ('TOP_UP', 'ALLOCATION', 'RETURN', 'PAYMENT')", name="ck_main_fund_movement_kind"))
