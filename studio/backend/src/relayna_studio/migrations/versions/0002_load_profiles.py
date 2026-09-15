"""Persist approved Chamber profiles per service/environment."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_load_profiles"
down_revision = "0001_studio_postgres"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "studio_load_profiles",
        sa.Column(
            "service_id", sa.Text(), sa.ForeignKey("studio_services.service_id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("environment", sa.String(128), primary_key=True),
        sa.Column("profile_id", sa.String(100), primary_key=True),
        sa.Column("profile", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("studio_load_profiles")
