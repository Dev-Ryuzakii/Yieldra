"""Keep domain tables private from the public Afribase Data API.

Revision ID: c608fbd1e3a2
Revises: b274a9c6e10f
"""
from alembic import op

revision = "c608fbd1e3a2"
down_revision = "b274a9c6e10f"
branch_labels = None
depends_on = None

TABLES = (
    "cold_storage_facilities", "crop_parameters", "users", "farms",
    "payout_accounts", "farm_plots", "harvests", "investments",
    "sponsorships", "cold_storage_bookings", "offtake_contracts",
    "sponsorship_milestones", "farmer_disbursements",
)


def upgrade() -> None:
    # FastAPI owns these tables and exposes only scoped API responses. No direct
    # browser writes or unrestricted PostgREST reads are needed.
    for table in TABLES:
        op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    for table in TABLES:
        op.execute(f'ALTER TABLE public."{table}" DISABLE ROW LEVEL SECURITY')
