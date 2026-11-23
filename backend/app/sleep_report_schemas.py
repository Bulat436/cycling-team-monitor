import csv
import io
import json
import re
from datetime import date, timedelta
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from .sleep_schemas import Minutes, SleepView
from .workout_report import ReportWorkout

MAX_SLEEP_REPORT_BYTES = 512 * 1024
MAX_SLEEP_REPORT_NIGHTS = 366
DATE_FORMAT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
TIMESTAMP_FORMAT = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})"
)


class SleepReportNight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sleep_date: date
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    duration_minutes: Minutes | None = None
    light_minutes: Minutes | None = None
    deep_minutes: Minutes | None = None
    rem_minutes: Minutes | None = None
    awake_minutes: Minutes | None = None
    sleep_score: Annotated[int, Field(strict=True, ge=0, le=100)] | None = None
    avg_heart_rate_bpm: Annotated[int, Field(strict=True, ge=1, le=300)] | None = None
    overnight_hrv_ms: Annotated[float, Field(strict=True, gt=0, le=1000, allow_inf_nan=False)] | None = None

    @field_validator("sleep_date", mode="before")
    @classmethod
    def calendar_date(cls, value):
        if not isinstance(value, str) or not DATE_FORMAT.fullmatch(value):
            raise ValueError("Expected YYYY-MM-DD")
        return value

    @field_validator("start_at", "end_at", mode="before")
    @classmethod
    def timestamp(cls, value):
        if value is not None and (not isinstance(value, str) or not TIMESTAMP_FORMAT.fullmatch(value)):
            raise ValueError("Expected an ISO timestamp with an offset")
        return value

    @model_validator(mode="after")
    def check_night(self):
        if not self.details():
            raise ValueError("At least one sleep observation is required")
        if self.start_at and (self.sleep_date - self.start_at.date()).days not in (0, 1):
            raise ValueError("Sleep started outside the reported night")
        if self.end_at and self.end_at.date() != self.sleep_date:
            raise ValueError("Wake date does not match sleep_date")
        if self.start_at and self.end_at and not timedelta(0) <= self.end_at - self.start_at <= timedelta(days=1):
            raise ValueError("Sleep interval must be between 0 and 24 hours")
        return self

    def details(self):
        return self.model_dump(mode="json", exclude={"sleep_date"}, exclude_none=True)


class SleepReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    sleep: list[SleepReportNight] = Field(min_length=1, max_length=MAX_SLEEP_REPORT_NIGHTS)


class ActivityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Annotated[int, Field(strict=True, ge=2, le=2)]
    sleep: list[SleepReportNight] = Field(default_factory=list, max_length=MAX_SLEEP_REPORT_NIGHTS)
    workouts: list[ReportWorkout] = Field(default_factory=list, max_length=MAX_SLEEP_REPORT_NIGHTS)

    @model_validator(mode="after")
    def require_records(self):
        if not self.sleep and not self.workouts:
            raise ValueError("Report is empty")
        return self


def csv_report(source: str) -> dict:
    reader = csv.DictReader(io.StringIO(source, newline=""), strict=True)
    fields = reader.fieldnames or []
    night_fields = SleepReportNight.model_fields.keys()
    workout_fields = ReportWorkout.model_fields.keys()
    if "kind" not in fields or len(set(fields)) != len(fields) or set(fields) - ({"kind"} | night_fields | workout_fields):
        raise ValueError("Invalid CSV header")
    result = {"schema_version": 2, "sleep": [], "workouts": []}
    timestamps = {"sleep_date", "start_at", "end_at", "started_at"}
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("Invalid CSV row size")
        kind = row.pop("kind").strip()
        if kind not in {"sleep", "workout"}:
            raise ValueError("Invalid CSV kind")
        allowed = night_fields if kind == "sleep" else workout_fields
        values = {name: value.strip() for name, value in row.items() if value.strip()}
        if values.keys() - allowed:
            raise ValueError("Invalid fields for CSV row kind")
        key = "sleep" if kind == "sleep" else "workouts"
        result[key].append({name: value if name in timestamps else json.loads(value) for name, value in values.items()})
        if len(result[key]) > MAX_SLEEP_REPORT_NIGHTS:
            raise ValueError("Too many records")
    return result


class SleepReportHistory(BaseModel):
    entries: list[SleepReportNight]
    has_more: bool


class SleepDetail(SleepView):
    report: SleepReportNight | None = None


class SleepReportResult(BaseModel):
    saved: int
    unchanged: int


class ActivityReportResult(SleepReportResult):
    workouts_saved: int
    workouts_unchanged: int
