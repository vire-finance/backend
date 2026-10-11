"""Add nullable structured destinations without rewriting existing payments.

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
"""
from alembic import op
import sqlalchemy as sa

revision = "c6d7e8f9a0b1"
down_revision = "b5c6d7e8f9a0"
branch_labels = depends_on = None


def upgrade():
    op.add_column("transactions", sa.Column("payment_type", sa.String(20), nullable=True))
    op.add_column("transactions", sa.Column("destination_name", sa.String(100), nullable=True))
    op.add_column("transactions", sa.Column("destination_account_number", sa.String(150), nullable=True))
    op.add_column("transactions", sa.Column("destination_bank_name", sa.String(100), nullable=True))


def downgrade():
    # Explicit downgrade discards only the new metadata, retaining the existing
    # recipient_account compatibility representation and financial records.
    for name in ("destination_bank_name", "destination_account_number", "destination_name", "payment_type"):
        op.drop_column("transactions", name)
