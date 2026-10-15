"""telegram link code

Revision ID: b6d2f0a8c915
Revises: a3c91e7d4b20
"""

import sqlalchemy as sa
from alembic import op

revision = "b6d2f0a8c915"
down_revision = "a3c91e7d4b20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("telegram_link_code", sa.String(length=12), nullable=True))
    op.add_column("users", sa.Column("telegram_link_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_telegram_link_code", "users", ["telegram_link_code"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_telegram_link_code", table_name="users")
    op.drop_column("users", "telegram_link_expires_at")
    op.drop_column("users", "telegram_link_code")
