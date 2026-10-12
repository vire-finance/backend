"""Add nonterminal invoice clarification and append-only review events."""
from alembic import op
import sqlalchemy as sa

revision = 'd7e8f9a0b1c2'
down_revision = 'c6d7e8f9a0b1'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE fundrequeststatus ADD VALUE IF NOT EXISTS 'NEEDS_CLARIFICATION'")
    op.create_table('request_events',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('request_id', sa.Uuid(), sa.ForeignKey('fund_requests.id'), nullable=False),
        sa.Column('actor_id', sa.Uuid(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('event_type', sa.String(40), nullable=False),
        sa.Column('status', sa.String(40), nullable=False),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('action_key', sa.Uuid(), nullable=True),
        sa.UniqueConstraint('request_id', 'action_key', name='uq_request_event_key'))
    op.create_index('ix_request_events_request_id', 'request_events', ['request_id'])


def downgrade():
    # Conversation and state are business records; require an explicit archival
    # and compatibility plan rather than silently destroying them.
    raise RuntimeError('Clarification history cannot be downgraded automatically; preserve review records.')
