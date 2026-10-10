"""Snapshot the expense category on each payment."""
from alembic import op
import sqlalchemy as sa
revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("transactions", sa.Column("category", sa.String(100), nullable=True))
    op.create_index("ix_transactions_category", "transactions", ["category"])
    op.execute("UPDATE transactions AS t SET category = COALESCE(c.category, 'Others') FROM cards AS c WHERE c.id = t.card_id")

def downgrade():
    op.drop_index("ix_transactions_category", table_name="transactions")
    op.drop_column("transactions", "category")
