"""farm assignment audit events

Revision ID: e2a7c4f19b38
Revises: c4e8b1a07d21
"""

import sqlalchemy as sa
from alembic import op

revision = "e2a7c4f19b38"
down_revision = "c4e8b1a07d21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "farm_assignment_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("farm_id", sa.Integer(), nullable=False),
        sa.Column("previous_farmer_id", sa.Integer(), nullable=True),
        sa.Column("new_farmer_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("actor_via", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("previous_owner_kind", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.ForeignKeyConstraint(["previous_farmer_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["new_farmer_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_farm_assignment_events_farm_id", "farm_assignment_events", ["farm_id"])
    if op.get_bind().dialect.name == "postgresql":
        # Same policy as the other domain tables: only FastAPI reads or writes it.
        op.execute('ALTER TABLE public."farm_assignment_events" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.drop_index("ix_farm_assignment_events_farm_id", table_name="farm_assignment_events")
    op.drop_table("farm_assignment_events")
