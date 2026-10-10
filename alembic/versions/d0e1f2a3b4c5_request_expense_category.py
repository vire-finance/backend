"""Store request expense categories independently of the paying card."""
from alembic import op
import sqlalchemy as sa

revision = 'd0e1f2a3b4c5'
down_revision = 'c9d0e1f2a3b4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('fund_requests', sa.Column('category', sa.String(100), nullable=False, server_default='Others'))
    op.create_index('ix_fund_requests_category', 'fund_requests', ['category'])


def downgrade():
    op.drop_index('ix_fund_requests_category', table_name='fund_requests')
    op.drop_column('fund_requests', 'category')
