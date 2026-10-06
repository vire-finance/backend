"""add user profile and security fields

Revision ID: a1b2c3d4e5f6
Revises: ded6986943bd
Create Date: 2026-10-06 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "7af5f5f0d67a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOTIFICATION_JSON = sa.JSON().with_variant(postgresql.JSONB, "postgresql")


def upgrade() -> None:
    # ── Profile fields ─────────────────────────────────────────────
    op.add_column(
        "users",
        sa.Column("full_name", sa.String(200), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("avatar_url", sa.String(2048), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "business_owner_id",
            sa.Uuid(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_users_business_owner_id", "users", ["business_owner_id"], unique=False
    )

    # ── Security / PIN fields ───────────────────────────────────────
    op.add_column(
        "users",
        sa.Column("security_pin", sa.String(255), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "pin_failed_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "users",
        sa.Column("pin_locked_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "biometric_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "notification_preferences",
            _NOTIFICATION_JSON,
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "notification_preferences")
    op.drop_column("users", "biometric_enabled")
    op.drop_column("users", "pin_locked_until")
    op.drop_column("users", "pin_failed_attempts")
    op.drop_column("users", "security_pin")
    op.drop_index("ix_users_business_owner_id", table_name="users")
    op.drop_column("users", "business_owner_id")
    op.drop_column("users", "avatar_url")
    op.drop_column("users", "full_name")
