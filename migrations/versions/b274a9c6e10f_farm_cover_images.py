"""Add sourced editorial cover images to farm listings.

Revision ID: b274a9c6e10f
Revises: a729e164b0da
"""
from alembic import op
import sqlalchemy as sa

revision = "b274a9c6e10f"
down_revision = "a729e164b0da"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("farms", sa.Column("cover_image_url", sa.String(500), nullable=True))
    op.add_column("farms", sa.Column("cover_image_alt", sa.String(200), nullable=True))
    op.add_column("farms", sa.Column("cover_image_credit", sa.String(200), nullable=True))
    op.add_column("farms", sa.Column("cover_image_source", sa.String(500), nullable=True))
    op.add_column("farms", sa.Column("cover_image_license", sa.String(80), nullable=True))


def downgrade() -> None:
    op.drop_column("farms", "cover_image_license")
    op.drop_column("farms", "cover_image_source")
    op.drop_column("farms", "cover_image_credit")
    op.drop_column("farms", "cover_image_alt")
    op.drop_column("farms", "cover_image_url")
