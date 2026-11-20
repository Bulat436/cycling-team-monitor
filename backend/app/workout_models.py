from sqlalchemy import JSON, ForeignKey, ForeignKeyConstraint, Index, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from .athlete_models import Recorded
from .models import Base


class WorkoutFile(Recorded, Base):
    __tablename__ = "workout_files"
    __table_args__ = (
        ForeignKeyConstraint(["workout_id", "athlete_id"], ["workouts.id", "workouts.athlete_id"], ondelete="CASCADE"),
        Index("ix_workout_files_decoded_hash", "athlete_id", "fit_sha256"),
    )

    athlete_id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    workout_id: Mapped[int] = mapped_column(index=True)
    fit_sha256: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(180))
    byte_count: Mapped[int]
    container: Mapped[str] = mapped_column(String(12))


class WorkoutSource(Recorded, Base):
    __tablename__ = "workout_sources"
    __table_args__ = (
        ForeignKeyConstraint(["workout_id", "athlete_id"], ["workouts.id", "workouts.athlete_id"], ondelete="CASCADE"),
    )

    athlete_id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(24), primary_key=True)
    source_key: Mapped[str] = mapped_column(String(300), primary_key=True)
    workout_id: Mapped[int] = mapped_column(index=True)
    external_id: Mapped[str | None] = mapped_column(String(256))
    file_sha256: Mapped[str] = mapped_column(String(64))


class WorkoutImport(Recorded, Base):
    __tablename__ = "workout_imports"
    __table_args__ = (Index("ix_workout_imports_athlete_id_id", "athlete_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athlete_profiles.athlete_id", ondelete="CASCADE"))
    workout_id: Mapped[int | None] = mapped_column(ForeignKey("workouts.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(24))
    filename: Mapped[str] = mapped_column(String(180))
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(40))
    candidates: Mapped[list[int]] = mapped_column(JSON)


class WorkoutSampleBlock(Recorded, Base):
    __tablename__ = "workout_samples"

    workout_id: Mapped[int] = mapped_column(ForeignKey("workouts.id", ondelete="CASCADE"), primary_key=True)
    block_index: Mapped[int] = mapped_column(primary_key=True)
    payload: Mapped[bytes] = mapped_column(LargeBinary)


class WorkoutMetrics(Recorded, Base):
    __tablename__ = "workout_metrics"

    workout_id: Mapped[int] = mapped_column(ForeignKey("workouts.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[str] = mapped_column(String(32))
    snapshot: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    metadata_json: Mapped[dict] = mapped_column(JSON)
