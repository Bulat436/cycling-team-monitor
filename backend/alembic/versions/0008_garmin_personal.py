"""Store isolated personal Garmin imports without changing the diary."""

import sqlalchemy as sa
from alembic import op

revision = "0008_garmin_personal"
down_revision = "0007_head_coach"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "garmin_personal_connection",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("installation_id", sa.String(32), nullable=False, unique=True),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("account_pk", sa.BigInteger),
        sa.Column("history_days", sa.Integer),
        sa.Column("request_interval_seconds", sa.Integer),
        sa.Column("last_request_at", sa.Float),
        sa.Column("blocked_until", sa.Float),
        sa.Column("last_success_at", sa.Float),
        sa.CheckConstraint("id = 1", name="ck_garmin_single_personal_connection"),
    )
    op.create_table(
        "garmin_sync_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("connection_id", sa.Integer, sa.ForeignKey("garmin_personal_connection.id", ondelete="CASCADE"), nullable=False),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date, nullable=False),
        sa.Column("started_at", sa.Float, nullable=False),
        sa.Column("ended_at", sa.Float),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("inserted", sa.Integer, nullable=False),
        sa.Column("unchanged", sa.Integer, nullable=False),
        sa.Column("skipped", sa.Integer, nullable=False),
    )
    op.create_table(
        "garmin_sleep_observations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("connection_id", sa.Integer, sa.ForeignKey("garmin_personal_connection.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(8), nullable=False, server_default="garmin"),
        sa.Column("source_key", sa.String(80), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("external_id", sa.BigInteger),
        sa.Column("garmin_date", sa.Date, nullable=False),
        sa.Column("sleep_date", sa.Date, nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("start_ms", sa.BigInteger, nullable=False),
        sa.Column("end_ms", sa.BigInteger, nullable=False),
        sa.Column("start_offset_minutes", sa.Integer),
        sa.Column("end_offset_minutes", sa.Integer),
        sa.Column("sleep_seconds", sa.Integer, nullable=False),
        sa.Column("deep_seconds", sa.Integer),
        sa.Column("light_seconds", sa.Integer),
        sa.Column("rem_seconds", sa.Integer),
        sa.Column("awake_seconds", sa.Integer),
        sa.Column("sleep_score", sa.Integer),
        sa.Column("average_heart_rate", sa.Float),
        sa.Column("received_at", sa.Float, nullable=False),
        sa.CheckConstraint("source = 'garmin'", name="ck_garmin_sleep_source"),
        sa.CheckConstraint("end_ms > start_ms AND sleep_seconds BETWEEN 0 AND 86400", name="ck_garmin_sleep_window"),
        sa.UniqueConstraint("connection_id", "source_key", "content_hash", name="uq_garmin_sleep_revision"),
    )
    op.create_index("ix_garmin_sleep_external_id", "garmin_sleep_observations", ["connection_id", "external_id"])
    op.create_index("ix_garmin_sleep_window", "garmin_sleep_observations", ["connection_id", "garmin_date", "start_ms", "end_ms"])
    op.create_index("ix_garmin_sleep_date", "garmin_sleep_observations", ["connection_id", "sleep_date"])


def downgrade():
    op.drop_table("garmin_sleep_observations")
    op.drop_table("garmin_sync_runs")
    op.drop_table("garmin_personal_connection")
