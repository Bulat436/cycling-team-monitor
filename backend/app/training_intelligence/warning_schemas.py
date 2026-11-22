from datetime import date as Date
from typing import Literal

from pydantic import BaseModel, Field

from ..analytics_schemas import DateRange
from .schemas import Coverage, LoadExposure, LoadReferenceBlock, LoadWeek, RecoveryDay, RecoveryPattern, Status

WarningType = Literal[
    "possible_accumulated_fatigue", "load_growth_with_worsening_recovery", "possible_performance_decline",
    "positive_adaptation_signs", "insufficient_data", "recovery_after_load_block",
]
Attention = Literal["informational", "moderate", "high"]


class WarningFact(BaseModel):
    metric: str
    unit: Literal["tss", "points", "minutes", "watts", "days", "weeks"]
    value: float | None
    baseline: float | None = None
    baseline_kind: Literal["individual", "previous_week", "required_minimum"] | None = None
    delta: float | None = None
    delta_pct: float | None = None
    period: DateRange | None = None
    reference_period: DateRange | None = None
    date: Date | None = None
    observations: int | None = None
    workout_id: int | None = None
    load_date: Date | None = None
    lag_days: int | None = None


class WarningWorkout(BaseModel):
    workout_id: int
    date: Date
    role: Literal["load", "control_attempt", "control_baseline"]


class WarningCoverage(BaseModel):
    status: Status
    load: Coverage | None = None
    load_status: Status | None = None
    load_reference_blocks: list[LoadReferenceBlock] = Field(default_factory=list)
    load_exposure: LoadExposure | None = None
    recovery_period: DateRange | None = None
    evaluated_recovery_dates: list[Date] = Field(default_factory=list)
    worsened_recovery_dates: list[Date] = Field(default_factory=list)
    diary_period: DateRange | None = None
    diary_filled_days: int | None = None
    missing_diary_dates: list[Date] = Field(default_factory=list)
    performance_baseline_attempts: int | None = None
    performance_recent_attempts: int | None = None
    gaps: list[str] = Field(default_factory=list)


class AnalyticsWarning(BaseModel):
    id: str
    episode_id: str | None = None
    related_warning_ids: list[str] = Field(default_factory=list)
    type: WarningType
    title: str
    attention: Attention
    period: DateRange
    workouts: list[WarningWorkout]
    facts: list[WarningFact]
    coverage: WarningCoverage
    explanation: str
    limitations: list[str]
    protocol: str | None = None
    duration_seconds: int | None = None
    thresholds: dict[str, float] = Field(default_factory=dict)


class AthleteWarnings(BaseModel):
    athlete_id: int
    timezone: str
    completed_through: Date
    current_week: LoadWeek
    recovery: RecoveryPattern
    performance_status: Status
    max_attention: Attention | None
    warnings: list[AnalyticsWarning]


class TeamWarningRow(AthleteWarnings):
    name: str
    group: str | None
    active: bool


class TeamWarnings(BaseModel):
    athletes: list[TeamWarningRow]


class OwnRecovery(BaseModel):
    athlete_id: int
    timezone: str
    completed_through: Date
    load: LoadWeek
    recovery: list[RecoveryDay]
    missing_diary_dates: list[Date]
    recovery_window_days: int
