"""Bind push devices to active sessions and checkpoint successful deliveries."""
from alembic import op
import sqlalchemy as sa

revision = 'e8f9a0b1c2d3'
down_revision = 'd7e8f9a0b1c2'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('push_devices', sa.Column('session_id', sa.Uuid(), nullable=True))
    op.create_foreign_key('fk_push_device_session', 'push_devices', 'auth_sessions', ['session_id'], ['id'], ondelete='CASCADE')
    op.create_index('ix_push_devices_session_id', 'push_devices', ['session_id'])
    op.create_table('push_deliveries',
        sa.Column('notification_id', sa.Uuid(), sa.ForeignKey('finance_notifications.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('device_id', sa.Uuid(), sa.ForeignKey('push_devices.id', ondelete='CASCADE'), primary_key=True))


def downgrade():
    op.drop_table('push_deliveries')
    op.drop_index('ix_push_devices_session_id', table_name='push_devices')
    op.drop_constraint('fk_push_device_session', 'push_devices', type_='foreignkey')
    op.drop_column('push_devices', 'session_id')
