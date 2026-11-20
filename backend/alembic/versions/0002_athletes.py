"""Athlete profiles, histories, wellness and workout RPE."""

from alembic import op
import sqlalchemy as sa

revision = "0002_athletes"
down_revision = "0001_access"
branch_labels = None
depends_on = None


def recorded():
    return (
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )


def athlete_key(*, primary_key=True):
    return sa.Column(
        "athlete_id", sa.Integer(), sa.ForeignKey("athlete_profiles.athlete_id", ondelete="CASCADE"),
        nullable=False, primary_key=primary_key,
    )


def upgrade() -> None:
    op.create_table(
        "athlete_profiles",
        sa.Column("athlete_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("birth_date", sa.Date()),
        sa.Column("height_cm", sa.Float()),
        sa.Column("specialization", sa.String(120)),
        sa.Column("team_group", sa.String(120)),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("coach_notes", sa.String(4000)),
        *recorded(),
        sa.CheckConstraint("height_cm > 0", name="ck_athlete_height"),
    )
    op.execute(
        "INSERT INTO athlete_profiles (athlete_id, timezone, created_at, updated_at) "
        "SELECT id, 'Europe/Moscow', created_at, updated_at FROM users WHERE role = 'athlete'"
    )
    op.create_table(
        "ftp_history",
        athlete_key(),
        sa.Column("effective_from", sa.Date(), primary_key=True),
        sa.Column("watts", sa.Integer(), nullable=False),
        *recorded(),
        sa.CheckConstraint("watts > 0", name="ck_ftp_watts"),
    )
    op.create_table(
        "weight_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        athlete_key(primary_key=False),
        sa.Column("measured_on", sa.Date(), nullable=False),
        sa.Column("kg", sa.Float(), nullable=False),
        *recorded(),
        sa.CheckConstraint("kg > 0", name="ck_weight_kg"),
    )
    op.create_index("ix_weight_history_athlete_date", "weight_history", ["athlete_id", "measured_on", "id"])
    for table in ("power_zone_profiles", "heart_rate_zone_profiles"):
        op.create_table(
            table, athlete_key(), sa.Column("zones", sa.JSON(), nullable=False), *recorded(),
        )
    fields = ("sleep_quality", "general_fatigue", "leg_fatigue", "motivation", "mood", "muscle_soreness")
    op.create_table(
        "wellness_entries",
        athlete_key(),
        sa.Column("entry_date", sa.Date(), primary_key=True),
        sa.Column("timezone", sa.String(64), nullable=False),
        *(sa.Column(field, sa.Integer(), nullable=False) for field in fields),
        sa.Column("illness_symptoms", sa.Boolean(), nullable=False),
        sa.Column("comment", sa.String(2000)),
        *recorded(),
        *(sa.CheckConstraint(f"{field} BETWEEN 1 AND 5", name=f"ck_wellness_{field}") for field in fields),
    )
    op.create_index("ix_wellness_entries_entry_date", "wellness_entries", ["entry_date"])
    op.create_table(
        "workouts",
        sa.Column("id", sa.Integer(), primary_key=True),
        athlete_key(primary_key=False),
        sa.Column("started_at", sa.Integer(), nullable=False),
        sa.Column("timer_seconds", sa.Float()),
        *recorded(),
        sa.CheckConstraint("timer_seconds >= 0", name="ck_workout_timer"),
    )
    op.create_index("ix_workouts_athlete_started", "workouts", ["athlete_id", "started_at", "id"])
    op.create_table(
        "rpe_entries",
        sa.Column("workout_id", sa.Integer(), sa.ForeignKey("workouts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("comment", sa.String(2000)),
        *recorded(),
        sa.CheckConstraint("score BETWEEN 0 AND 10", name="ck_rpe_score"),
    )


def downgrade() -> None:
    for table in (
        "rpe_entries", "workouts", "wellness_entries", "heart_rate_zone_profiles",
        "power_zone_profiles", "weight_history", "ftp_history", "athlete_profiles",
    ):
        op.drop_table(table)
