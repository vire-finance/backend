"""Deploy existing notification model once protected request tables exist.

This migration intentionally does not create or alter protected tables.
"""
from alembic import context, op
import sqlalchemy as sa

revision = "c3d4e5f6a7b8"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not context.is_offline_mode() and not sa.inspect(bind).has_table("fund_requests"):
        raise RuntimeError("Finance schema prerequisite missing: fund_requests. Apply finance migration d4e5f6a7b8c9 before notifications.")
    if not context.is_offline_mode() and sa.inspect(bind).has_table("finance_notifications"):
        # Existing metadata-created installations must be reconciled explicitly.
        raise RuntimeError("finance_notifications already exists; reconcile its migration history before upgrading.")
    op.create_table("finance_notifications",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("request_id", sa.Uuid(), sa.ForeignKey("fund_requests.id"), nullable=True),
        sa.Column("title", sa.String(150), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.create_index("ix_finance_notifications_user_id", "finance_notifications", ["user_id"])


def downgrade():
    op.drop_index("ix_finance_notifications_user_id", table_name="finance_notifications")
    op.drop_table("finance_notifications")
