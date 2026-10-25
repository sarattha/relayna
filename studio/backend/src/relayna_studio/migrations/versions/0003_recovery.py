"""Add the approved durable Recovery Center ledger and failure reservations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_recovery"
down_revision = "0003_incident_inbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "studio_recovery_plans",
        sa.Column("plan_id", sa.String(36), primary_key=True),
        sa.Column("document", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column("next_poll_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_studio_recovery_poll", "studio_recovery_plans", ["next_poll_at"])
    op.create_table(
        "studio_recovery_claims",
        sa.Column("service_id", sa.Text(), primary_key=True),
        sa.Column("environment", sa.Text(), primary_key=True),
        sa.Column("failure_id", sa.Text(), primary_key=True),
        sa.Column("plan_id", sa.String(36), sa.ForeignKey("studio_recovery_plans.plan_id"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("studio_recovery_claims")
    op.drop_index("ix_studio_recovery_poll", table_name="studio_recovery_plans")
    op.drop_table("studio_recovery_plans")
