from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .athlete_schemas import Score

TeamSleepSource = Literal["manual", "legacy", "automatic"]
Minutes = Annotated[int, Field(strict=True, ge=0, le=1440)]
Offset = Annotated[int, Field(strict=True, ge=-840, le=840)]
LocalTime = Annotated[str, StringConstraints(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")]
Version = Annotated[int, Field(strict=True, ge=0)]


class ManualSleepEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Version
    start_time: LocalTime | None = None
    end_time: LocalTime | None = None
    start_offset_minutes: Offset | None = None
    end_offset_minutes: Offset | None = None
    duration_minutes: Minutes | None = None
    sleep_quality: Score | None = None
    recovery: Score | None = None


class SleepSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Version
    source: TeamSleepSource | None


class SleepMove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Version
    date: date
    start_offset_minutes: Offset | None = None
    end_offset_minutes: Offset | None = None


class SleepView(BaseModel):
    date: date
    timezone: str
    version: int
    updated_at: float | None
    duration_minutes: int | None
    source: TeamSleepSource | None
    selected_source: TeamSleepSource | None
    conflict: bool
    manually_corrected: bool
    candidates: dict[TeamSleepSource, int]
    start_time: str | None
    end_time: str | None
    start_offset_minutes: int | None
    end_offset_minutes: int | None
    entered_duration_minutes: int | None
    calculated_duration_minutes: int | None
    sleep_quality: int | None
    recovery: int | None
    has_diary: bool
    has_report: bool = False


class SleepHistory(BaseModel):
    timezone: str
    local_date: date
    entries: list[SleepView]
    has_more: bool
