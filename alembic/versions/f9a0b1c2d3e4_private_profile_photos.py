"""Persist private profile photo references without using OCR job records."""
from alembic import op
import sqlalchemy as sa

revision = 'f9a0b1c2d3e4'
down_revision = 'e8f9a0b1c2d3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('profile_photos',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id'), nullable=False, unique=True),
        sa.Column('storage_path', sa.String(1024), nullable=False),
        sa.Column('stored_filename', sa.String(100), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=False),
        sa.Column('mime_type', sa.String(50), nullable=False),
        sa.Column('original_filename', sa.String(100), nullable=False))


def downgrade():
    raise RuntimeError('Profile photo references require an explicit archival plan before downgrade.')
