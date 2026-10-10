"""Persist the owner-selected expense categories allowed on each card."""
from alembic import op
import sqlalchemy as sa

revision = 'f2a3b4c5d6e7'
down_revision = 'e1f2a3b4c5d6'
branch_labels = None
depends_on = None


def upgrade():
    # Existing cards keep all categories until an owner explicitly configures them.
    op.add_column('cards', sa.Column('allowed_categories', sa.JSON(), nullable=False, server_default=sa.text("'[\"Salary\", \"Operational\", \"Production\", \"Marketing\", \"Emergency\", \"Administration & Tax\", \"Others\"]'")))


def downgrade():
    op.drop_column('cards', 'allowed_categories')
