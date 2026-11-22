from datetime import date

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base


class GarminPersonalConnection(Base):
    __tablename__ = "garmin_personal_connection"
    __table_args__ = (CheckConstraint("id = 1", name="ck_garmin_single_personal_connection"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    installation_id: Mapped[str] = mapped_column(String(32), unique=True)
    timezone: Mapped[str] = mapped_column(String(64))
    account_pk: Mapped[int | None] = mapped_column(BigInteger)
    history_days: Mapped[int | None]
    request_interval_seconds: Mapped[int | None]
    last_request_at: Mapped[float | None]
    blocked_until: Mapped[float | None]
    last_success_at: Mapped[float | None]


class GarminSyncRun(Base):
    __tablename__ = "garmin_sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("garmin_personal_connection.id", ondelete="CASCADE"))
    start_date: Mapped[date]
    end_date: Mapped[date]
    started_at: Mapped[float]
    ended_at: Mapped[float | None]
    status: Mapped[str] = mapped_column(String(32))
    inserted: Mapped[int] = mapped_column(default=0)
    unchanged: Mapped[int] = mapped_column(default=0)
    skipped: Mapped[int] = mapped_column(default=0)


class GarminSleepObservation(Base):
    __tablename__ = "garmin_sleep_observations"
    __table_args__ = (
        CheckConstraint("source = 'garmin'", name="ck_garmin_sleep_source"),
        CheckConstraint("end_ms > start_ms AND sleep_seconds BETWEEN 0 AND 86400", name="ck_garmin_sleep_window"),
        UniqueConstraint("connection_id", "source_key", "content_hash", name="uq_garmin_sleep_revision"),
        Index("ix_garmin_sleep_external_id", "connection_id", "external_id"),
        Index("ix_garmin_sleep_window", "connection_id", "garmin_date", "start_ms", "end_ms"),
        Index("ix_garmin_sleep_date", "connection_id", "sleep_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("garmin_personal_connection.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(8), default="garmin", server_default="garmin")
    source_key: Mapped[str] = mapped_column(String(80))
    content_hash: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[int | None] = mapped_column(BigInteger)
    garmin_date: Mapped[date]
    sleep_date: Mapped[date]
    timezone: Mapped[str] = mapped_column(String(64))
    start_ms: Mapped[int] = mapped_column(BigInteger)
    end_ms: Mapped[int] = mapped_column(BigInteger)
    start_offset_minutes: Mapped[int | None]
    end_offset_minutes: Mapped[int | None]
    sleep_seconds: Mapped[int]
    deep_seconds: Mapped[int | None]
    light_seconds: Mapped[int | None]
    rem_seconds: Mapped[int | None]
    awake_seconds: Mapped[int | None]
    sleep_score: Mapped[int | None]
    average_heart_rate: Mapped[float | None]
    received_at: Mapped[float]
