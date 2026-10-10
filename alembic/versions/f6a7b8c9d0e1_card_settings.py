"""Add expiry date and card usage settings."""
from alembic import op
import sqlalchemy as sa

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("cards", sa.Column("expires_on", sa.Date(), nullable=True))
    op.add_column("cards", sa.Column("usage_type", sa.String(20), nullable=False, server_default="LONG_TERM"))
    op.add_column("cards", sa.Column("single_use_consumed_at", sa.DateTime(), nullable=True))
    op.create_check_constraint("ck_card_usage_type", "cards", "usage_type IN ('SINGLE_USE', 'LONG_TERM', 'SUBSCRIPTION')")

def downgrade():
    op.drop_constraint("ck_card_usage_type", "cards", type_="check")
    op.drop_column("cards", "single_use_consumed_at")
    op.drop_column("cards", "usage_type")
    op.drop_column("cards", "expires_on")
