from datetime import date
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from .athlete_schemas import WellnessEntryView
from .onelap_routes import ConnectionView


class AlertCode(StrEnum):
    DIARY_MISSING = "diary_missing"
    ILLNESS = "illness"
    FATIGUE = "fatigue"
    SORENESS = "soreness"
    LOW_MOTIVATION = "low_motivation"
    RPE_MISSING = "rpe_missing"
    SYNC_ERROR = "sync_error"
    INACTIVE = "inactive_training"


class AlertSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: set[AlertCode] = Field(default_factory=lambda: set(AlertCode))
    diary_due_hour: Annotated[int, Field(strict=True, ge=0, le=23)] = 10
    fatigue_min: Annotated[int, Field(strict=True, ge=1, le=5)] = 4
    soreness_min: Annotated[int, Field(strict=True, ge=1, le=5)] = 4
    motivation_max: Annotated[int, Field(strict=True, ge=1, le=5)] = 2
    rpe_grace_hours: Annotated[int, Field(strict=True, ge=0, le=168)] = 24
    rpe_lookback_days: Annotated[int, Field(strict=True, ge=7, le=90)] = 14
    inactive_days: Annotated[int, Field(strict=True, ge=1, le=90)] = 7


class Advisory(BaseModel):
    code: AlertCode
    value: int | None = None


class DateRange(BaseModel):
    start: date
    end: date


class Load(BaseModel):
    workouts: int
    timer_seconds: float | None
    distance_m: float | None
    tss: float | None
    rpe_load: float | None
    tss_count: int
    rpe_count: int
    rpe_load_count: int


class LastWorkout(BaseModel):
    id: int
    started_at: int
    timer_seconds: float | None
    distance_m: float | None
    rpe: int | None


class TeamRow(BaseModel):
    athlete_id: int
    name: str
    group: str | None
    timezone: str
    today: date
    account: str
    period: DateRange
    wellness: WellnessEntryView | None
    last_workout: LastWorkout | None
    load: Load
    onelap: ConnectionView
    alerts: list[Advisory]


class TeamOverview(BaseModel):
    total: int
    active: int
    pending: int
    filled: int
    alerted: int
    groups: list[str]
    rows: list[TeamRow]


class WellnessPoint(BaseModel):
    date: date
    scores: dict[str, int] | None
    baseline: dict[str, float] | None


class WorkoutPoint(BaseModel):
    id: int
    date: date
    started_at: int
    timer_seconds: float | None
    elapsed_seconds: float | None
    distance_m: float | None
    avg_power: float | None
    np: float | None
    tss: float | None
    rpe: int | None
    rpe_load: float | None
    reasons: dict[str, str]


class FtpPoint(BaseModel):
    date: date
    watts: int
    effective_from: date


class WeekPoint(Load):
    start: date
    end: date


class ZoneBand(BaseModel):
    name: str
    minimum: int
    maximum: int | None
    seconds: int


class ZoneDistribution(BaseModel):
    bands: list[ZoneBand]
    outside_seconds: int | None
    unknown_seconds: int | None
    workouts_with_data: int
    workouts: int
    omitted: dict[str, int]


class AthleteAnalytics(BaseModel):
    period: DateRange
    timezone: str
    load: Load
    wellness: list[WellnessPoint]
    workouts: list[WorkoutPoint]
    weeks: list[WeekPoint]
    ftp: list[FtpPoint]
    power_zones: ZoneDistribution
    heart_rate_zones: ZoneDistribution


class SamplePoint(BaseModel):
    second: int
    value: float | None


class WorkoutSeries(BaseModel):
    power: list[SamplePoint]
    heart_rate: list[SamplePoint]
    cadence: list[SamplePoint]
