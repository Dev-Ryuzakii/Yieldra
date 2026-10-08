"""Link verified Afribase identities to local user roles.

Revision ID: a729e164b0da
Revises: f180724a6109
"""
from alembic import op
import sqlalchemy as sa

revision = "a729e164b0da"
down_revision = "f180724a6109"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("afribase_uid", sa.String(100), nullable=True))
    op.create_index("ix_users_afribase_uid", "users", ["afribase_uid"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_afribase_uid", table_name="users")
    op.drop_column("users", "afribase_uid")
