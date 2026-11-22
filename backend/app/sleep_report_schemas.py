import re
from datetime import date, timedelta
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from .sleep_schemas import Minutes

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


class SleepReportHistory(BaseModel):
    entries: list[SleepReportNight]
    has_more: bool


class SleepReportResult(BaseModel):
    saved: int
    unchanged: int
