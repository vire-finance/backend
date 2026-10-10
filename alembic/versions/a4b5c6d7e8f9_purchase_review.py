"""Purchase approval and post-payment receipt lifecycle."""
from alembic import op
import sqlalchemy as sa
revision = "a4b5c6d7e8f9"
down_revision = "f2a3b4c5d6e7"
branch_labels = depends_on = None
def upgrade():
    op.add_column("fund_requests", sa.Column("payment_executor", sa.String(30), nullable=False, server_default="OWNER_PAYMENT"))
    for column in [sa.Column("card_id", sa.UUID(), sa.ForeignKey("cards.id")), sa.Column("payment_method", sa.String(30), nullable=False, server_default="QRIS"), sa.Column("recipient_account", sa.String(150)), sa.Column("receipt_status", sa.String(30)), sa.Column("receipt_due_at", sa.DateTime()), sa.Column("paid_at", sa.DateTime()), sa.Column("receipt_document_id", sa.UUID(), sa.ForeignKey("ocr_documents.id")), sa.Column("receipt_note", sa.Text()), sa.Column("receipt_review_reason", sa.Text()), sa.Column("receipt_overdue_notified", sa.Boolean(), nullable=False, server_default=sa.false())]:
        op.add_column("fund_requests", column)
    op.add_column("transactions", sa.Column("payment_method", sa.String(30), nullable=False, server_default="QRIS"))
    op.add_column("transactions", sa.Column("recipient_account", sa.String(150)))
    op.add_column("finance_notifications", sa.Column("push_processed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("finance_notifications", sa.Column("push_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("finance_notifications", sa.Column("push_retry_at", sa.DateTime()))
    op.create_table("push_devices", sa.Column("id", sa.UUID(), primary_key=True), sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False, index=True), sa.Column("token", sa.String(500), nullable=False, unique=True), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False))
def downgrade():
    op.drop_column("fund_requests", "payment_executor")
    op.drop_table("push_devices")
    for name in ["push_retry_at", "push_attempts", "push_processed"]: op.drop_column("finance_notifications", name)
    for name in ["recipient_account", "payment_method"]: op.drop_column("transactions", name)
    for name in ["receipt_overdue_notified", "receipt_review_reason", "receipt_note", "receipt_document_id", "paid_at", "receipt_due_at", "receipt_status", "recipient_account", "payment_method", "card_id"]: op.drop_column("fund_requests", name)
