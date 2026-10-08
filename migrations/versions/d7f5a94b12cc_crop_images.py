"""Store licensed crop image metadata alongside Afribase Storage objects.

Revision ID: d7f5a94b12cc
Revises: c608fbd1e3a2
"""
from alembic import op
import sqlalchemy as sa

revision = "d7f5a94b12cc"
down_revision = "c608fbd1e3a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("crop_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("crop", sa.String(80), nullable=False),
        sa.Column("image_url", sa.String(500), nullable=False),
        sa.Column("alt", sa.String(200), nullable=False),
        sa.Column("credit", sa.String(200), nullable=False),
        sa.Column("source_url", sa.String(500), nullable=False),
        sa.Column("license", sa.String(80), nullable=False),
        sa.Column("changes", sa.String(200), nullable=False),
        sa.Column("illustrative", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_crop_images_crop", "crop_images", ["crop"], unique=True)
    op.execute('ALTER TABLE public."crop_images" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.drop_index("ix_crop_images_crop", table_name="crop_images")
    op.drop_table("crop_images")
