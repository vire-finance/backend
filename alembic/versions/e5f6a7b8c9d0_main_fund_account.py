"""Add simulated main funding accounts without changing existing pocket balances."""
from alembic import op
import sqlalchemy as sa
revision = "e5f6a7b8c9d0"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("main_fund_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("account_type", sa.String(20), nullable=False),
        sa.Column("provider_name", sa.String(100), nullable=False),
        sa.Column("account_number", sa.String(40), nullable=False),
        sa.Column("account_holder", sa.String(150), nullable=False),
        sa.Column("balance", sa.Numeric(18, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("balance >= 0 AND balance <= 1000000000000000", name="ck_main_fund_balance"),
        sa.CheckConstraint("account_type IN ('BANK', 'E_WALLET')", name="ck_main_fund_type"))
    op.create_table("main_fund_movements",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("main_fund_accounts.id"), nullable=False),
        sa.Column("pocket_id", sa.Uuid(), sa.ForeignKey("pockets.id"), nullable=True),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("balance_after", sa.Numeric(18, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_main_fund_movement_key"),
        sa.CheckConstraint("amount > 0", name="ck_main_fund_movement_amount"),
        sa.CheckConstraint("kind IN ('TOP_UP', 'ALLOCATION', 'RETURN')", name="ck_main_fund_movement_kind"))
    op.create_index("ix_main_fund_movements_owner_id", "main_fund_movements", ["owner_id"])
    op.alter_column("simulated_topups", "pocket_id", nullable=True)

def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT count(*) FROM simulated_topups WHERE pocket_id IS NULL")):
        raise RuntimeError("Main fund top-ups exist; reconcile them before downgrading.")
    op.alter_column("simulated_topups", "pocket_id", nullable=False)
    op.drop_table("main_fund_movements")
    op.drop_table("main_fund_accounts")
