import re
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select

from .athlete_models import Workout
from .athlete_schemas import WorkoutSummary
from .workout_models import WorkoutMetrics, WorkoutSource

Measurement = Annotated[float, Field(strict=True, ge=0, le=1_000_000_000, allow_inf_nan=False)]
Seconds = Annotated[float, Field(strict=True, ge=0, le=7 * 86400, allow_inf_nan=False)]
START_FORMAT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:Z|[+-][0-9]{2}:[0-9]{2})")


class ReportWorkout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    started_at: datetime
    timer_seconds: Seconds
    elapsed_seconds: Seconds | None = None
    moving_seconds: Seconds | None = None
    distance_m: Measurement | None = None
    avg_speed: Measurement | None = None
    max_speed: Measurement | None = None
    ascent_m: Measurement | None = None
    descent_m: Measurement | None = None
    avg_power: Measurement | None = None
    max_power: Measurement | None = None
    avg_heart_rate: Annotated[float, Field(strict=True, gt=0, le=300, allow_inf_nan=False)] | None = None
    max_heart_rate: Annotated[float, Field(strict=True, gt=0, le=300, allow_inf_nan=False)] | None = None
    avg_cadence: Measurement | None = None
    max_cadence: Measurement | None = None
    calories: Measurement | None = None
    avg_temperature: Annotated[float, Field(strict=True, ge=-100, le=100)] | None = None
    max_temperature: Annotated[float, Field(strict=True, ge=-100, le=100)] | None = None
    min_altitude_m: Annotated[float, Field(strict=True, ge=-12000, le=50000)] | None = None
    max_altitude_m: Annotated[float, Field(strict=True, ge=-12000, le=50000)] | None = None
    np: Measurement | None = None
    if_value: Annotated[float, Field(strict=True, ge=0, le=10)] | None = None
    tss: Measurement | None = None

    @field_validator("started_at", mode="before")
    @classmethod
    def check_start(cls, value):
        if not isinstance(value, str) or not START_FORMAT.fullmatch(value):
            raise ValueError("Expected a timestamp with seconds and a UTC offset")
        instant = datetime.fromisoformat(value)
        if instant.utcoffset() is None or instant.microsecond or not 1 <= instant.timestamp() <= 253402214399:
            raise ValueError("Invalid workout start")
        return instant

    @model_validator(mode="after")
    def check_duration(self):
        if self.elapsed_seconds is not None and self.elapsed_seconds < self.timer_seconds:
            raise ValueError("Elapsed time is less than timer time")
        return self


def report_workout_key(started_at: int) -> str:
    return f"start:{started_at}"


def store_workout(db, service, profile, report: ReportWorkout) -> bool:
    athlete_id = profile.athlete_id
    started_at = int(report.started_at.timestamp())
    key = report_workout_key(started_at)
    source = db.get(WorkoutSource, (athlete_id, "report", key))
    canonical = report.model_copy(update={"started_at": report.started_at.astimezone(UTC)})
    digest = sha256(canonical.model_dump_json().encode()).hexdigest()
    if source:
        workout = db.get(Workout, source.workout_id)
        if source.file_sha256 == digest:
            return False
    else:
        candidates = db.scalars(select(Workout).where(
            Workout.athlete_id == athlete_id, Workout.started_at == started_at,
        )).all()
        if len(candidates) > 1:
            raise HTTPException(409, "report_workout_conflict")
        workout = candidates[0] if candidates else None
        if workout is None:
            workout = Workout(athlete_id=athlete_id, started_at=started_at)
            db.add(workout)
            db.flush()
        elif workout.file_sha256 is None:
            # Existing activities with a different source need explicit reconciliation.
            raise HTTPException(409, "report_workout_conflict")
        source = WorkoutSource(athlete_id=athlete_id, source="report", source_key=key, workout_id=workout.id)
        db.add(source)

    source.file_sha256 = digest
    if workout.file_sha256 is not None:
        return True

    workout.timer_seconds = report.timer_seconds
    values = report.model_dump()
    workout.summary = WorkoutSummary(
        **{name: values.get(name) for name in WorkoutSummary.model_fields if name not in {"gps", "sample_count", "sports"}},
        gps=False, sample_count=0, sports=["cycling"],
    ).model_dump()
    metrics = db.get(WorkoutMetrics, workout.id)
    if metrics is None:
        snapshot = service._snapshot(db, profile, started_at)
        metrics = WorkoutMetrics(workout_id=workout.id, snapshot={**snapshot, "algorithm": {}})
        db.add(metrics)
    metrics.version = "report-summary-v1"
    metrics.result = {
        "np": report.np, "if_value": report.if_value, "tss": report.tss,
        "power_zones": None, "heart_rate_zones": None, "power_distribution": None,
        "quality": {"timing_source": "report", "expected_seconds": None, "covered_seconds": {},
                    "np_windows": 0, "possible_np_windows": 0, "missing_channels": []},
        "reasons": {},
    }
    metrics.metadata_json = {}
    return True
