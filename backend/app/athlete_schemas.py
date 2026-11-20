from datetime import date
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

Score = Annotated[int, Field(strict=True, ge=1, le=5)]
RpeScore = Annotated[int, Field(strict=True, ge=0, le=10)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Comment = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
Timezone = Annotated[str, StringConstraints(min_length=1, max_length=64)]
Bound = Annotated[int, Field(strict=True, ge=0, le=5000)]
ZoneKind = Literal["power", "heart-rate"]


class ProfileEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ShortText
    birth_date: date | None = None
    height_cm: Annotated[float, Field(strict=True, gt=0, le=300)] | None = None
    specialization: ShortText | None = None
    team_group: ShortText | None = None
    timezone: Timezone
    coach_notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)] | None = None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Use a valid IANA time zone") from exc
        return value


class FtpValue(BaseModel):
    watts: Annotated[int, Field(strict=True, gt=0, le=3000)]


class WeightValue(BaseModel):
    measured_on: date
    kg: Annotated[float, Field(strict=True, gt=0, le=500)]


class FtpMeasurement(FtpValue):
    model_config = ConfigDict(from_attributes=True)

    effective_from: date
    created_at: float
    updated_at: float


class WeightMeasurement(WeightValue):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: float
    updated_at: float


class Zone(BaseModel):
    name: ShortText
    minimum: Bound
    maximum: Bound | None

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.maximum is not None and self.maximum <= self.minimum:
            raise ValueError("Maximum must exceed minimum")
        return self


class Zones(BaseModel):
    zones: Annotated[list[Zone], Field(max_length=12)]

    @model_validator(mode="after")
    def validate_order(self):
        for previous, current in zip(self.zones, self.zones[1:]):
            if previous.maximum is None or current.minimum < previous.maximum:
                raise ValueError("Zones must be ordered and must not overlap")
        return self


class ProfileSummary(BaseModel):
    athlete_id: int
    name: str
    active: bool
    team_group: str | None
    timezone: str


class ProfileDetail(ProfileSummary):
    email: str
    birth_date: date | None
    height_cm: float | None
    specialization: str | None
    coach_notes: str | None
    local_date: date
    indicators_updated_at: float | None
    current_ftp: FtpMeasurement | None
    current_weight: WeightMeasurement | None
    ftp_history: list[FtpMeasurement]
    weight_history: list[WeightMeasurement]
    power_zones: list[Zone]
    heart_rate_zones: list[Zone]


class WellnessValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sleep_quality: Score
    general_fatigue: Score
    leg_fatigue: Score
    motivation: Score
    mood: Score
    muscle_soreness: Score
    illness_symptoms: Annotated[bool, Field(strict=True)]
    comment: Comment | None = None


class WellnessEntryView(WellnessValue):
    model_config = ConfigDict(from_attributes=True)

    entry_date: date
    timezone: str
    created_at: float
    updated_at: float


class WellnessHistory(BaseModel):
    timezone: str
    local_date: date
    today_entry: WellnessEntryView | None
    entries: list[WellnessEntryView]
    has_more: bool


class TeamWellness(ProfileSummary):
    local_date: date
    entry: WellnessEntryView | None


class RpeValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: RpeScore
    comment: Comment | None = None


class RpeView(RpeValue):
    workout_id: int
    session_load: float | None
    created_at: float
    updated_at: float


class WorkoutView(BaseModel):
    id: int
    athlete_id: int
    started_at: int
    timer_seconds: float | None
    rpe: RpeView | None


class WorkoutList(BaseModel):
    timezone: str
    workouts: list[WorkoutView]
    missing_rpe_count: int
    has_more: bool
