from datetime import date
from time import time

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base

DEFAULT_TIMEZONE = "Europe/Moscow"


class Recorded:
    # Fractional UTC epoch seconds preserve the times of quick successive edits.
    created_at: Mapped[float] = mapped_column(default=time)
    updated_at: Mapped[float] = mapped_column(default=time, onupdate=time)


class AthleteProfile(Recorded, Base):
    __tablename__ = "athlete_profiles"
    __table_args__ = (CheckConstraint("height_cm > 0", name="ck_athlete_height"),)

    athlete_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    birth_date: Mapped[date | None]
    height_cm: Mapped[float | None]
    specialization: Mapped[str | None] = mapped_column(String(120))
    team_group: Mapped[str | None] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64), default=DEFAULT_TIMEZONE)
    coach_notes: Mapped[str | None] = mapped_column(String(4000))


class FtpRecord(Recorded, Base):
    __tablename__ = "ftp_history"
    __table_args__ = (CheckConstraint("watts > 0", name="ck_ftp_watts"),)

    athlete_id: Mapped[int] = mapped_column(ForeignKey("athlete_profiles.athlete_id", ondelete="CASCADE"), primary_key=True)
    effective_from: Mapped[date] = mapped_column(primary_key=True)
    watts: Mapped[int]


class WeightRecord(Recorded, Base):
    __tablename__ = "weight_history"
    __table_args__ = (
        CheckConstraint("kg > 0", name="ck_weight_kg"),
        Index("ix_weight_history_athlete_date", "athlete_id", "measured_on", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athlete_profiles.athlete_id", ondelete="CASCADE"))
    measured_on: Mapped[date]
    kg: Mapped[float]


class ZoneProfile(Recorded):
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athlete_profiles.athlete_id", ondelete="CASCADE"), primary_key=True)
    zones: Mapped[list[dict]] = mapped_column(JSON)


class PowerZoneProfile(ZoneProfile, Base):
    __tablename__ = "power_zone_profiles"


class HeartRateZoneProfile(ZoneProfile, Base):
    __tablename__ = "heart_rate_zone_profiles"


WELLNESS_SCORES = (
    "sleep_quality", "general_fatigue", "leg_fatigue",
    "motivation", "mood", "muscle_soreness",
)


class WellnessEntry(Recorded, Base):
    __tablename__ = "wellness_entries"
    __table_args__ = tuple(
        CheckConstraint(f"{field} BETWEEN 1 AND 5", name=f"ck_wellness_{field}")
        for field in WELLNESS_SCORES
    )

    athlete_id: Mapped[int] = mapped_column(ForeignKey("athlete_profiles.athlete_id", ondelete="CASCADE"), primary_key=True)
    entry_date: Mapped[date] = mapped_column(primary_key=True, index=True)
    timezone: Mapped[str] = mapped_column(String(64))
    sleep_quality: Mapped[int]
    general_fatigue: Mapped[int]
    leg_fatigue: Mapped[int]
    motivation: Mapped[int]
    mood: Mapped[int]
    muscle_soreness: Mapped[int]
    illness_symptoms: Mapped[bool]
    comment: Mapped[str | None] = mapped_column(String(2000))


class Workout(Recorded, Base):
    __tablename__ = "workouts"
    __table_args__ = (
        CheckConstraint("timer_seconds >= 0", name="ck_workout_timer"),
        Index("ix_workouts_athlete_started", "athlete_id", "started_at", "id"),
        Index("ux_workouts_id_athlete", "id", "athlete_id", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athlete_profiles.athlete_id", ondelete="CASCADE"))
    started_at: Mapped[int]
    timer_seconds: Mapped[float | None]
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[dict | None] = mapped_column(JSON)


class RpeEntry(Recorded, Base):
    __tablename__ = "rpe_entries"
    __table_args__ = (CheckConstraint("score BETWEEN 0 AND 10", name="ck_rpe_score"),)

    workout_id: Mapped[int] = mapped_column(ForeignKey("workouts.id", ondelete="CASCADE"), primary_key=True)
    score: Mapped[int]
    comment: Mapped[str | None] = mapped_column(String(2000))
