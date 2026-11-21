from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ..analytics_schemas import DateRange, FtpPoint
from ..fit_parser import MAX_ACTIVITY_SECONDS

Status = Literal["sufficient", "partial", "insufficient", "unsupported", "missing"]
DayStatus = Literal["rest", "all_workouts_recorded", "imported", "unknown", "conflict"]
Confirmation = Literal["rest", "all_workouts_recorded"]


class Rules(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    atl_days: Annotated[int, Field(ge=1, le=42)] = 7
    ctl_days: Annotated[int, Field(ge=7, le=90)] = 42
    load_baseline_min_days: Annotated[int, Field(ge=3, le=28)] = 14
    weekly_min_days: Annotated[int, Field(ge=5, le=7)] = 5
    weekly_min_baseline_blocks: Annotated[int, Field(ge=3, le=4)] = 3
    high_load_pct: Annotated[float, Field(gt=0, le=500)] = 20
    intense_if: Annotated[float, Field(gt=0, le=3)] = 0.85
    intense_min_seconds: Annotated[int, Field(ge=1, le=86400)] = 1200
    recovery_window_days: Annotated[int, Field(ge=7, le=90)] = 28
    recovery_min_observations: Annotated[int, Field(ge=3, le=90)] = 7
    recovery_score_delta: Annotated[float, Field(gt=0, le=4)] = 1
    recovery_sleep_delta_minutes: Annotated[int, Field(ge=1, le=1440)] = 45
    recovery_min_groups: Annotated[int, Field(ge=2, le=6)] = 2
    recovery_min_evaluated_days: Annotated[int, Field(ge=2, le=7)] = 4
    recovery_min_worsened_days: Annotated[int, Field(ge=2, le=7)] = 2
    recovery_normalization_days: Annotated[int, Field(ge=2, le=7)] = 2
    performance_change_pct: Annotated[float, Field(gt=0, le=100)] = 5
    performance_baseline_attempts: Annotated[int, Field(ge=3, le=30)] = 3
    performance_repeat_attempts: Annotated[int, Field(ge=2, le=7)] = 2
    performance_window_days: Annotated[int, Field(ge=7, le=180)] = 90
    correlation_min_pairs: Annotated[int, Field(ge=30, le=180)] = 30
    correlation_min_half_abs: Annotated[float, Field(gt=0, le=1)] = 0.1
    warning_high_worsened_days: Annotated[int, Field(ge=2, le=7)] = 3
    warning_growth_pct: Annotated[float, Field(gt=0, le=500)] = 15
    warning_growth_recent_days: Annotated[int, Field(ge=1, le=7)] = 3
    warning_recent_days: Annotated[int, Field(ge=1, le=45)] = 14
    warning_diary_window_days: Annotated[int, Field(ge=7, le=45)] = 14
    warning_diary_min_entries: Annotated[int, Field(ge=1, le=45)] = 7

    @model_validator(mode="after")
    def check_windows(self):
        if self.atl_days >= self.ctl_days or self.recovery_min_observations > self.recovery_window_days:
            raise ValueError("Invalid smoothing periods or observation window")
        if self.warning_diary_min_entries > self.warning_diary_window_days:
            raise ValueError("Diary minimum exceeds the warning window")
        if self.performance_baseline_attempts > self.performance_window_days:
            raise ValueError("Performance minimum exceeds the comparison window")
        if (self.recovery_min_worsened_days > self.recovery_min_evaluated_days
                or self.warning_high_worsened_days < self.recovery_min_worsened_days):
            raise ValueError("Invalid thresholds for repeated deterioration")
        return self


class Coverage(BaseModel):
    days: int
    known_days: int
    confirmed_days: int
    workouts: int
    workouts_with_tss: int


class WorkoutLoad(BaseModel):
    workout_id: int
    date: date
    started_at: int
    timer_seconds: float | None
    np: float | None
    if_value: float | None
    tss: float | None
    rpe: int | None
    session_rpe: float | None
    intense: bool
    duplicate_candidates: list[int] = Field(default_factory=list)
    reasons: dict[str, str] = Field(default_factory=dict)
    best_power: dict[str, float | None] | None = None


class LoadDay(BaseModel):
    date: date
    day_status: DayStatus
    confirmation: Confirmation | None
    coverage: Coverage
    recorded_tss: float | None
    tss: float | None
    timer_seconds: float | None
    session_rpe: float | None
    intense_workouts: int
    workout_ids: list[int]
    preliminary: bool
    baseline: float | None = None
    baseline_observations: int = 0
    delta_tss: float | None = None
    delta_pct: float | None = None
    elevated_streak: int = 0
    atl: float | None = None
    ctl: float | None = None
    tsb: float | None = None
    atl_coverage: Coverage | None = None
    ctl_coverage: Coverage | None = None
    atl_status: Status = "missing"
    ctl_status: Status = "missing"
    tsb_status: Status = "missing"


class LoadReferenceBlock(BaseModel):
    period: DateRange
    coverage: Coverage
    tss: float


class LoadWeek(BaseModel):
    period: DateRange
    coverage: Coverage
    recorded_tss: float | None
    timer_seconds: float | None
    session_rpe: float | None
    intense_workouts: int
    rest_days: int
    baseline_blocks: list[LoadReferenceBlock]
    baseline_tss: float | None
    comparable_tss: float | None
    delta_tss: float | None
    delta_pct: float | None
    previous_week_tss: float | None
    change_from_previous_pct: float | None
    status: Status


class RecoveryValue(BaseModel):
    value: int | None
    baseline: float | None
    observations: int
    delta: float | None
    status: Status
    deteriorated: bool | None
    within_baseline: bool | None


class RecoveryDay(BaseModel):
    date: date
    has_entry: bool
    metrics: dict[str, RecoveryValue]


class RecoveryPattern(BaseModel):
    evaluated_dates: list[date]
    deterioration_dates: list[date]
    sustained: bool | None


class EffortValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    start_seconds: Annotated[int, Field(strict=True, ge=0, lt=MAX_ACTIVITY_SECONDS)]
    duration_seconds: Annotated[int, Field(strict=True, ge=1, le=MAX_ACTIVITY_SECONDS)]

    @model_validator(mode="after")
    def check_interval(self):
        if self.start_seconds + self.duration_seconds > MAX_ACTIVITY_SECONDS:
            raise ValueError("Interval exceeds FIT activity limit")
        return self


class EffortView(EffortValue):
    workout_id: int
    mean_watts: float | None
    status: Status
    reason: str | None = None


class EffortPoint(EffortView):
    date: date
    baseline_watts: float | None = None
    baseline_workout_ids: list[int] = Field(default_factory=list)
    delta_pct: float | None = None
    rpe: int | None = None
    baseline_rpe: float | None = None
    rpe_observations: int = 0


class PerformanceChange(BaseModel):
    protocol: str
    duration_seconds: int
    direction: Literal["decreased", "improved"]
    threshold_pct: float
    baseline_watts: float
    baseline_workout_ids: list[int]
    baseline_attempts: list[EffortPoint] = Field(default_factory=list)
    workouts: list[EffortPoint]


class Performance(BaseModel):
    status: Status
    efforts: list[EffortPoint]
    repeated_changes: list[PerformanceChange]
    ftp: list[FtpPoint]
    planned_intensity: Literal["unsupported"] = "unsupported"


class Reaction(BaseModel):
    date: date
    metric: str
    value: float
    baseline: float
    delta: float
    workout_id: int | None = None
    observations: int | None = None


class LinkedReaction(Reaction):
    load_date: date
    lag_days: int
    lag_group: Literal["0", "1", "2", "3", "4-7"]
    same_date_only: bool


class LoadExposure(BaseModel):
    date: date
    kind: Literal["daily", "weekly"]
    period: DateRange
    tss: float
    baseline_tss: float
    delta_tss: float
    delta_pct: float
    baseline_observations: int
    coverage: Coverage
    status: Status


class LoadEpisode(BaseModel):
    period: DateRange
    elevated_dates: list[date]
    workout_ids: list[int]
    reaction_window_end: date
    open: bool
    reactions: list[LinkedReaction]
    exposures: list[LoadExposure] = Field(default_factory=list)
    normalization_dates: list[date] = Field(default_factory=list)


class Correlation(BaseModel):
    metric: str
    lag_days: int
    pairs: int
    confirmed_load_pairs: int
    period: DateRange | None
    coefficient: float | None
    stable: bool | None
    halves: tuple[float | None, float | None] | None = None
    status: Status


class Measurements(BaseModel):
    athlete_id: int
    timezone: str
    period: DateRange
    completed_through: date
    rules: Rules
    workouts: list[WorkoutLoad]
    load: list[LoadDay]
    weeks: list[LoadWeek]
    current_week: LoadWeek
    recovery: list[RecoveryDay]
    recovery_pattern: RecoveryPattern
    missing_diary_dates: list[date]
    unsupported_recovery: list[str]
    performance: Performance
    episodes: list[LoadEpisode]
    correlations: list[Correlation]
    relationship_limitations: list[str]
