"""cencori photo checks

Revision ID: c4e8b1a07d21
Revises: b6d2f0a8c915
"""

import sqlalchemy as sa
from alembic import op

revision = "c4e8b1a07d21"
down_revision = "b6d2f0a8c915"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "photo_checks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("farm_id", sa.Integer(), nullable=False),
        sa.Column("milestone_id", sa.Integer(), nullable=False),
        sa.Column("stage_key", sa.String(length=40), nullable=False),
        sa.Column("crop_type", sa.String(length=80), nullable=False),
        sa.Column("milestone_title", sa.String(length=120), nullable=False),
        sa.Column("evidence_required", sa.Text(), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.String(length=40), nullable=False),
        sa.Column("stored_name", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("superseded", sa.Boolean(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("provider_mode", sa.String(length=20), nullable=True),
        sa.Column("model_name", sa.String(length=80), nullable=True),
        sa.Column("model_provider", sa.String(length=40), nullable=True),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observations", sa.JSON(), nullable=True),
        sa.Column("issues", sa.JSON(), nullable=True),
        sa.Column("limitations", sa.JSON(), nullable=True),
        sa.Column("recommended_disposition", sa.String(length=32), nullable=True),
        sa.Column("model_reported_confidence", sa.Float(), nullable=True),
        sa.Column("uncertainty_notes", sa.Text(), nullable=True),
        sa.Column("visible_text_notes", sa.Text(), nullable=True),
        sa.Column("failure_code", sa.String(length=40), nullable=True),
        sa.Column("submitted_by_role", sa.String(length=20), nullable=False),
        sa.Column("submitted_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.ForeignKeyConstraint(["milestone_id"], ["sponsorship_milestones.id"]),
        sa.ForeignKeyConstraint(["submitted_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("milestone_id", "evidence_sha256", name="uq_photo_check_milestone_digest"),
    )
    op.create_index("ix_photo_checks_farm_id", "photo_checks", ["farm_id"])
    op.create_index("ix_photo_checks_milestone_id", "photo_checks", ["milestone_id"])
    op.create_index("ix_photo_checks_status", "photo_checks", ["status"])
    op.create_table(
        "photo_check_reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("photo_check_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("actor_role", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["photo_check_id"], ["photo_checks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_photo_check_reviews_photo_check_id", "photo_check_reviews", ["photo_check_id"])


def downgrade() -> None:
    op.drop_index("ix_photo_check_reviews_photo_check_id", table_name="photo_check_reviews")
    op.drop_table("photo_check_reviews")
    op.drop_index("ix_photo_checks_status", table_name="photo_checks")
    op.drop_index("ix_photo_checks_milestone_id", table_name="photo_checks")
    op.drop_index("ix_photo_checks_farm_id", table_name="photo_checks")
    op.drop_table("photo_checks")
