"""Optional diary measurements and protocol annotations for existing FIT workouts."""

import sqlalchemy as sa
from alembic import op

revision = "0006_training_intelligence"
down_revision = "0005_coach"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("wellness_entries", sa.Column("sleep_minutes", sa.Integer(), sa.CheckConstraint(
        "sleep_minutes BETWEEN 0 AND 1440", name="ck_wellness_sleep_minutes",
    ), nullable=True))
    op.add_column("wellness_entries", sa.Column("recovery", sa.Integer(), sa.CheckConstraint(
        "recovery BETWEEN 1 AND 5", name="ck_wellness_recovery",
    ), nullable=True))
    op.add_column("wellness_entries", sa.Column("training_day_status", sa.String(24), sa.CheckConstraint(
        "training_day_status IN ('rest', 'all_workouts_recorded')", name="ck_wellness_training_day_status",
    ), nullable=True))
    op.create_table(
        "workout_efforts",
        sa.Column("workout_id", sa.Integer(), sa.ForeignKey("workouts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("protocol", sa.String(64), nullable=False),
        sa.Column("start_seconds", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("projection_version", sa.String(32), nullable=False),
        sa.Column("mean_watts", sa.Float(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.CheckConstraint("start_seconds >= 0 AND duration_seconds > 0", name="ck_effort_interval"),
        sa.CheckConstraint("mean_watts >= 0", name="ck_effort_watts"),
    )


def downgrade():
    op.drop_table("workout_efforts")
    with op.batch_alter_table("wellness_entries") as batch:
        batch.drop_constraint("ck_wellness_training_day_status", type_="check")
        batch.drop_constraint("ck_wellness_recovery", type_="check")
        batch.drop_constraint("ck_wellness_sleep_minutes", type_="check")
        batch.drop_column("training_day_status")
        batch.drop_column("recovery")
        batch.drop_column("sleep_minutes")
