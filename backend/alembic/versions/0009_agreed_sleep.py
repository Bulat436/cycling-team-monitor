"""Keep manual sleep and historical duration as separate candidates."""

import sqlalchemy as sa
from alembic import op

revision = "0009_agreed_sleep"
down_revision = "0008_garmin_personal"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sleep_days",
        sa.Column("athlete_id", sa.Integer, sa.ForeignKey("athlete_profiles.athlete_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("sleep_date", sa.Date, primary_key=True),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("legacy_minutes", sa.Integer),
        sa.Column("manual_minutes", sa.Integer),
        sa.Column("calculated_minutes", sa.Integer),
        sa.Column("start_time", sa.String(5)),
        sa.Column("end_time", sa.String(5)),
        sa.Column("start_offset_minutes", sa.Integer),
        sa.Column("end_offset_minutes", sa.Integer),
        sa.Column("sleep_quality", sa.Integer),
        sa.Column("recovery", sa.Integer),
        sa.Column("selected_source", sa.String(8)),
        sa.Column("duration_cleared", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.Float, nullable=False),
        sa.CheckConstraint("legacy_minutes BETWEEN 0 AND 1440", name="ck_sleep_legacy_minutes"),
        sa.CheckConstraint("manual_minutes BETWEEN 0 AND 1440", name="ck_sleep_manual_minutes"),
        sa.CheckConstraint("calculated_minutes BETWEEN 0 AND 1440", name="ck_sleep_calculated_minutes"),
        sa.CheckConstraint("sleep_quality BETWEEN 1 AND 5", name="ck_sleep_quality"),
        sa.CheckConstraint("recovery BETWEEN 1 AND 5", name="ck_sleep_recovery"),
        sa.CheckConstraint("selected_source IN ('manual', 'legacy')", name="ck_sleep_selected_source"),
        sa.CheckConstraint("version > 0", name="ck_sleep_version"),
    )
    op.execute("""
        INSERT INTO sleep_days (athlete_id, sleep_date, timezone, legacy_minutes, version, updated_at)
        SELECT athlete_id, entry_date, timezone, sleep_minutes, 1, updated_at
        FROM wellness_entries WHERE sleep_minutes IS NOT NULL
    """)


def downgrade():
    op.drop_table("sleep_days")
