"""Add a PIN version for invalidating verification tokens after PIN changes."""
from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("security_pin_version", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("users", "security_pin_version")
