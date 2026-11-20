from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from .athlete_models import Recorded
from .models import Base


class OnelapConnection(Recorded, Base):
    __tablename__ = "onelap_connections"

    athlete_id: Mapped[int] = mapped_column(ForeignKey("athlete_profiles.athlete_id", ondelete="CASCADE"), primary_key=True)
    generation: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(32))
    secret: Mapped[str | None]
    account_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    history_days: Mapped[int]
    watermark: Mapped[int | None]
    connect_attempt_at: Mapped[float | None]
    last_success_at: Mapped[float | None]
    next_sync_at: Mapped[float | None] = mapped_column(index=True)
    failures: Mapped[int] = mapped_column(default=0)
    diagnostic: Mapped[str | None] = mapped_column(String(40))


class SyncRun(Recorded, Base):
    __tablename__ = "sync_runs"
    __table_args__ = (Index("ix_sync_runs_athlete_id_id", "athlete_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("onelap_connections.athlete_id", ondelete="CASCADE"))
    generation: Mapped[int]
    trigger: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(20), index=True)
    started_at: Mapped[float | None]
    ended_at: Mapped[float | None]
    imported: Mapped[int] = mapped_column(default=0)
    duplicates: Mapped[int] = mapped_column(default=0)
    rejected: Mapped[int] = mapped_column(default=0)
    diagnostic: Mapped[str | None] = mapped_column(String(40))


class SyncError(Recorded, Base):
    __tablename__ = "sync_errors"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("sync_runs.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40))


class OnelapRevision(Recorded, Base):
    __tablename__ = "onelap_workout_versions"

    athlete_id: Mapped[int] = mapped_column(ForeignKey("onelap_connections.athlete_id", ondelete="CASCADE"), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision_hash: Mapped[str] = mapped_column(String(64))


class OnelapGate(Base):
    __tablename__ = "onelap_request_gate"
    __table_args__ = (CheckConstraint("id = 1", name="ck_onelap_gate_singleton"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    next_request_at: Mapped[float]
    blocked_until: Mapped[float]
