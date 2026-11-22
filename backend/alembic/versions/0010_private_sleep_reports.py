"""Store uploaded sleep nights outside the shared diary and analytics."""

import sqlalchemy as sa
from alembic import op

revision = "0010_private_sleep_reports"
down_revision = "0009_agreed_sleep"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "private_sleep_report_nights",
        sa.Column("athlete_id", sa.Integer, sa.ForeignKey("athlete_profiles.athlete_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("sleep_date", sa.Date, primary_key=True),
        sa.Column("details", sa.JSON, nullable=False),
        sa.Column("imported_at", sa.Float, nullable=False),
    )


def downgrade():
    op.drop_table("private_sleep_report_nights")
