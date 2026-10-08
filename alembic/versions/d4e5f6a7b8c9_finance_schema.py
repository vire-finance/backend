"""Deploy the existing finance models without resetting business data.

Pocket limits start at allocated_amount. Legacy Card limits start at
max(balance + spent, 1 IDR), preserving their recorded balance/spent values.
Monthly usage is calculated from ledger dates; no fabricated legacy ledger
entries are created. Card category remains null for existing cards.
"""
from alembic import op
import sqlalchemy as sa

revision = "d4e5f6a7b8c9"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def identity_columns():
    return [sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False)]


def indexes(table, columns):
    for column in columns:
        op.create_index("ix_" + table + "_" + column, table, [column])


def upgrade():
    op.add_column("pockets", sa.Column("monthly_limit", sa.Numeric(18, 2), nullable=True))
    op.execute("UPDATE pockets SET monthly_limit = allocated_amount")
    op.alter_column("pockets", "monthly_limit", nullable=False)
    op.create_check_constraint("ck_pocket_monthly_limit_positive", "pockets", "monthly_limit > 0")

    op.add_column("cards", sa.Column("monthly_limit", sa.Numeric(18, 2), nullable=True))
    op.execute("UPDATE cards SET monthly_limit = GREATEST(balance + spent, 1)")
    op.alter_column("cards", "monthly_limit", nullable=False)
    op.create_check_constraint("ck_card_monthly_limit_positive", "cards", "monthly_limit > 0")
    op.add_column("cards", sa.Column("category", sa.String(100), nullable=True))
    op.create_index("ix_cards_category", "cards", ["category"])
    # No FROZEN value is used within this migration transaction.
    op.execute("ALTER TYPE cardstatus ADD VALUE IF NOT EXISTS 'FROZEN'")

    op.create_table("fund_requests",
        sa.Column("requester_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("pocket_id", sa.Uuid(), sa.ForeignKey("pockets.id"), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("request_type", sa.Enum("CASH_ADVANCE", "PURCHASE", "OTHER", name="fundrequesttype"), nullable=False),
        sa.Column("party_name", sa.String(150), nullable=False),
        sa.Column("total_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("needed_by", sa.DateTime(), nullable=False),
        sa.Column("status", sa.Enum("DRAFT", "PENDING_APPROVAL", "APPROVED", "COMPLETED", "REJECTED", name="fundrequeststatus"), nullable=False),
        sa.Column("ai_analysis", sa.Text(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        *identity_columns(),
        sa.CheckConstraint("total_amount > 0", name="ck_fund_request_amount_positive"))
    indexes("fund_requests", ["requester_id", "pocket_id", "request_type", "status", "reviewed_by"])

    op.create_table("fund_request_documents",
        sa.Column("fund_request_id", sa.Uuid(), sa.ForeignKey("fund_requests.id"), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_url", sa.String(500), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("ocr_text", sa.Text(), nullable=True),
        sa.Column("ocr_processed", sa.Boolean(), nullable=False),
        *identity_columns())
    indexes("fund_request_documents", ["fund_request_id"])

    op.create_table("transactions",
        sa.Column("pocket_id", sa.Uuid(), sa.ForeignKey("pockets.id"), nullable=False),
        sa.Column("card_id", sa.Uuid(), sa.ForeignKey("cards.id"), nullable=False),
        sa.Column("fund_request_id", sa.Uuid(), sa.ForeignKey("fund_requests.id"), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("transaction_type", sa.Enum("FUND_REQUEST", "CARD_PAYMENT", name="transactiontype"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        *identity_columns(),
        sa.CheckConstraint("amount > 0", name="ck_transaction_amount_positive"),
        sa.CheckConstraint("status IN ('PENDING', 'APPROVED', 'DECLINED', 'FAILED')", name="ck_transaction_status"),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_transaction_actor_key"))
    indexes("transactions", ["pocket_id", "card_id", "fund_request_id", "actor_id", "status"])
    op.create_index("uq_approved_transaction_per_request", "transactions", ["fund_request_id"], unique=True,
        postgresql_where=sa.text("status = 'APPROVED' AND fund_request_id IS NOT NULL"))

    op.create_table("simulated_topups",
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("pocket_id", sa.Uuid(), sa.ForeignKey("pockets.id"), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        *identity_columns(),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_topup_owner_key"),
        sa.CheckConstraint("amount > 0", name="ck_topup_amount_positive"),
        sa.CheckConstraint("method IN ('QR_CODE', 'BANK_TRANSFER')", name="ck_topup_method"),
        sa.CheckConstraint("status IN ('PENDING', 'COMPLETED')", name="ck_topup_status"))
    indexes("simulated_topups", ["owner_id", "pocket_id"])

    op.create_table("manual_invoice_payments",
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), sa.ForeignKey("transactions.id"), nullable=False, unique=True),
        sa.Column("request_id", sa.Uuid(), sa.ForeignKey("fund_requests.id"), nullable=False, unique=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("simulated_paid_at", sa.DateTime(), nullable=True),
        *identity_columns(),
        sa.CheckConstraint("status IN ('QUEUED', 'SIMULATED_PAID')", name="ck_invoice_payment_status"))
    indexes("manual_invoice_payments", ["owner_id"])


def downgrade():
    # Explicit downgrade removes new ledger tables; it is never invoked by startup.
    for table in ["manual_invoice_payments", "simulated_topups", "transactions", "fund_request_documents", "fund_requests"]:
        op.drop_table(table)
    op.execute("DROP TYPE transactiontype")
    op.execute("DROP TYPE fundrequeststatus")
    op.execute("DROP TYPE fundrequesttype")
    op.drop_index("ix_cards_category", table_name="cards")
    op.drop_column("cards", "category")
    op.drop_constraint("ck_card_monthly_limit_positive", "cards", type_="check")
    op.drop_column("cards", "monthly_limit")
    op.drop_constraint("ck_pocket_monthly_limit_positive", "pockets", type_="check")
    op.drop_column("pockets", "monthly_limit")
    # PostgreSQL cannot remove a single enum label safely; leave additive FROZEN.
