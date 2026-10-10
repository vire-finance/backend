"""Record main account debits only when a payment succeeds."""
from alembic import op

revision = 'e1f2a3b4c5d6'
down_revision = 'd0e1f2a3b4c5'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint('ck_main_fund_movement_kind', 'main_fund_movements', type_='check')
    op.create_check_constraint('ck_main_fund_movement_kind', 'main_fund_movements', "kind IN ('TOP_UP', 'ALLOCATION', 'RETURN', 'PAYMENT')")


def downgrade():
    # Refuse rollback if payment movements exist; keep the ledger intact.
    op.drop_constraint('ck_main_fund_movement_kind', 'main_fund_movements', type_='check')
    op.create_check_constraint('ck_main_fund_movement_kind', 'main_fund_movements', "kind IN ('TOP_UP', 'ALLOCATION', 'RETURN')")
