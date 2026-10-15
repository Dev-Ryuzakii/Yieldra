"""sponsorship plots

Revision ID: a3c91e7d4b20
Revises: d7f5a94b12cc
"""

import sqlalchemy as sa
from alembic import op

revision = "a3c91e7d4b20"
down_revision = "d7f5a94b12cc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sponsorships", sa.Column("plots", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("sponsorships", "plots")
