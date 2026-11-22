from datetime import date

from sqlalchemy import JSON, CheckConstraint, ForeignKey, String, false
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base


class SleepDay(Base):
    __tablename__ = "sleep_days"
    __table_args__ = (
        CheckConstraint("legacy_minutes BETWEEN 0 AND 1440", name="ck_sleep_legacy_minutes"),
        CheckConstraint("manual_minutes BETWEEN 0 AND 1440", name="ck_sleep_manual_minutes"),
        CheckConstraint("calculated_minutes BETWEEN 0 AND 1440", name="ck_sleep_calculated_minutes"),
        CheckConstraint("sleep_quality BETWEEN 1 AND 5", name="ck_sleep_quality"),
        CheckConstraint("recovery BETWEEN 1 AND 5", name="ck_sleep_recovery"),
        CheckConstraint("selected_source IN ('manual', 'legacy')", name="ck_sleep_selected_source"),
        CheckConstraint("version > 0", name="ck_sleep_version"),
    )

    athlete_id: Mapped[int] = mapped_column(ForeignKey("athlete_profiles.athlete_id", ondelete="CASCADE"), primary_key=True)
    sleep_date: Mapped[date] = mapped_column(primary_key=True)
    timezone: Mapped[str] = mapped_column(String(64))
    legacy_minutes: Mapped[int | None]
    manual_minutes: Mapped[int | None]
    calculated_minutes: Mapped[int | None]
    start_time: Mapped[str | None] = mapped_column(String(5))
    end_time: Mapped[str | None] = mapped_column(String(5))
    start_offset_minutes: Mapped[int | None]
    end_offset_minutes: Mapped[int | None]
    sleep_quality: Mapped[int | None]
    recovery: Mapped[int | None]
    selected_source: Mapped[str | None] = mapped_column(String(8))
    duration_cleared: Mapped[bool] = mapped_column(default=False, server_default=false())
    version: Mapped[int]
    updated_at: Mapped[float]


class PrivateSleepReportNight(Base):
    __tablename__ = "private_sleep_report_nights"

    athlete_id: Mapped[int] = mapped_column(ForeignKey("athlete_profiles.athlete_id", ondelete="CASCADE"), primary_key=True)
    sleep_date: Mapped[date] = mapped_column(primary_key=True)
    details: Mapped[dict] = mapped_column(JSON)
    imported_at: Mapped[float]
