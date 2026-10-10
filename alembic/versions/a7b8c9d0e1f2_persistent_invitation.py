"""Persist a seed for reproducible owner invitation codes."""
from alembic import op
import sqlalchemy as sa
revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("users", sa.Column("invite_code_seed", sa.String(64), nullable=True))

def downgrade():
    op.drop_column("users", "invite_code_seed")
