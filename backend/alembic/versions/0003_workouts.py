"""FIT originals, provenance, time series and calculation snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0003_workouts"
down_revision = "0002_athletes"
branch_labels = None
depends_on = None


def recorded():
    return (
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )


def workout_owner():
    return sa.ForeignKeyConstraint(["workout_id", "athlete_id"], ["workouts.id", "workouts.athlete_id"], ondelete="CASCADE")


def upgrade():
    op.add_column("workouts", sa.Column("file_sha256", sa.String(64)))
    op.add_column("workouts", sa.Column("summary", sa.JSON()))
    op.create_index("ux_workouts_id_athlete", "workouts", ["id", "athlete_id"], unique=True)
    op.create_table(
        "workout_files",
        sa.Column("athlete_id", sa.Integer(), primary_key=True),
        sa.Column("sha256", sa.String(64), primary_key=True),
        sa.Column("workout_id", sa.Integer(), nullable=False),
        sa.Column("fit_sha256", sa.String(64), nullable=False),
        sa.Column("filename", sa.String(180), nullable=False),
        sa.Column("byte_count", sa.Integer(), nullable=False),
        sa.Column("container", sa.String(12), nullable=False),
        *recorded(), workout_owner(),
    )
    op.create_index("ix_workout_files_workout_id", "workout_files", ["workout_id"])
    op.create_index("ix_workout_files_decoded_hash", "workout_files", ["athlete_id", "fit_sha256"])
    op.create_table(
        "workout_sources",
        sa.Column("athlete_id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(24), primary_key=True),
        sa.Column("source_key", sa.String(300), primary_key=True),
        sa.Column("workout_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(256)),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        *recorded(), workout_owner(),
    )
    op.create_index("ix_workout_sources_workout_id", "workout_sources", ["workout_id"])
    op.create_table(
        "workout_imports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("athlete_id", sa.Integer(), sa.ForeignKey("athlete_profiles.athlete_id", ondelete="CASCADE"), nullable=False),
        sa.Column("workout_id", sa.Integer(), sa.ForeignKey("workouts.id", ondelete="SET NULL")),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("filename", sa.String(180), nullable=False),
        sa.Column("file_sha256", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(40)),
        sa.Column("candidates", sa.JSON(), nullable=False),
        *recorded(),
    )
    op.create_index("ix_workout_imports_athlete_id_id", "workout_imports", ["athlete_id", "id"])
    op.create_table(
        "workout_samples",
        sa.Column("workout_id", sa.Integer(), sa.ForeignKey("workouts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("block_index", sa.Integer(), primary_key=True),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        *recorded(),
    )
    op.create_table(
        "workout_metrics",
        sa.Column("workout_id", sa.Integer(), sa.ForeignKey("workouts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        *recorded(),
    )


def downgrade():
    for table in ("workout_metrics", "workout_samples", "workout_imports", "workout_sources", "workout_files"):
        op.drop_table(table)
    op.drop_index("ux_workouts_id_athlete", table_name="workouts")
    op.drop_column("workouts", "summary")
    op.drop_column("workouts", "file_sha256")
