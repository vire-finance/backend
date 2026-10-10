"""Add a unique Google identity without replacing passwords or account roles."""
from alembic import op
import sqlalchemy as sa

revision = 'c9d0e1f2a3b4'
down_revision = 'b8c9d0e1f2a3'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('google_subject', sa.String(255), nullable=True))
    op.create_unique_constraint('uq_users_google_subject', 'users', ['google_subject'])


def downgrade():
    op.drop_constraint('uq_users_google_subject', 'users', type_='unique')
    op.drop_column('users', 'google_subject')
