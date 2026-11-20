"""Encrypted Onelap sessions, durable sync runs and request limits."""

import sqlalchemy as sa
from alembic import op

revision = "0004_onelap"
down_revision = "0003_workouts"
branch_labels = None
depends_on = None


def recorded():
    return (sa.Column("created_at", sa.Float(), nullable=False), sa.Column("updated_at", sa.Float(), nullable=False))


def upgrade():
    op.create_table(
        "onelap_connections",
        sa.Column("athlete_id", sa.Integer(), sa.ForeignKey("athlete_profiles.athlete_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("secret", sa.String()),
        sa.Column("account_hash", sa.String(64), unique=True),
        sa.Column("history_days", sa.Integer(), nullable=False),
        sa.Column("watermark", sa.Integer()),
        sa.Column("connect_attempt_at", sa.Float()),
        sa.Column("last_success_at", sa.Float()),
        sa.Column("next_sync_at", sa.Float()),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("diagnostic", sa.String(40)),
        *recorded(),
    )
    op.create_index("ix_onelap_connections_next_sync_at", "onelap_connections", ["next_sync_at"])
    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("athlete_id", sa.Integer(), sa.ForeignKey("onelap_connections.athlete_id", ondelete="CASCADE"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(12), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.Float()),
        sa.Column("ended_at", sa.Float()),
        sa.Column("imported", sa.Integer(), nullable=False),
        sa.Column("duplicates", sa.Integer(), nullable=False),
        sa.Column("rejected", sa.Integer(), nullable=False),
        sa.Column("diagnostic", sa.String(40)),
        *recorded(),
    )
    op.create_index("ix_sync_runs_athlete_id_id", "sync_runs", ["athlete_id", "id"])
    op.create_index("ix_sync_runs_status", "sync_runs", ["status"])
    op.create_table(
        "sync_errors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("sync_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        *recorded(),
    )
    op.create_index("ix_sync_errors_run_id", "sync_errors", ["run_id"])
    op.create_table(
        "onelap_workout_versions",
        sa.Column("athlete_id", sa.Integer(), sa.ForeignKey("onelap_connections.athlete_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("source_id", sa.String(64), primary_key=True),
        sa.Column("revision_hash", sa.String(64), nullable=False),
        *recorded(),
    )
    op.create_table(
        "onelap_request_gate",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("next_request_at", sa.Float(), nullable=False),
        sa.Column("blocked_until", sa.Float(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_onelap_gate_singleton"),
    )


def downgrade():
    for table in ("onelap_request_gate", "onelap_workout_versions", "sync_errors", "sync_runs", "onelap_connections"):
        op.drop_table(table)
