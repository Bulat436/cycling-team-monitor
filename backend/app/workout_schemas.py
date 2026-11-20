from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .athlete_schemas import WorkoutView, Zone


class ZoneTime(Zone):
    seconds: int


class ZoneTotals(BaseModel):
    zones: list[ZoneTime]
    outside_seconds: int
    unknown_seconds: int


class PowerBin(BaseModel):
    minimum: int
    maximum: int
    seconds: int


class MetricQuality(BaseModel):
    timing_source: str
    expected_seconds: int | None
    covered_seconds: dict[str, int]
    np_windows: int
    possible_np_windows: int
    missing_channels: list[str]


class MetricResult(BaseModel):
    np: float | None
    if_value: float | None
    tss: float | None
    power_zones: ZoneTotals | None
    heart_rate_zones: ZoneTotals | None
    power_distribution: list[PowerBin] | None
    quality: MetricQuality
    reasons: dict[str, str]


class CalculationSnapshot(BaseModel):
    timezone: str
    local_date: date
    ftp_watts: int | None
    ftp_effective_from: date | None
    power_zones: list[Zone]
    heart_rate_zones: list[Zone]
    algorithm: dict[str, float]


class MetricsView(BaseModel):
    version: str
    snapshot: CalculationSnapshot
    result: MetricResult


class SourceView(BaseModel):
    source: str
    external_id: str | None


class WorkoutDetail(WorkoutView):
    timezone: str
    metrics: MetricsView | None
    sources: list[SourceView]
    original_available: bool
    metadata: dict | None


class SamplePage(BaseModel):
    samples: list[dict]
    offset: int
    total: int


class ImportView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    workout_id: int
    status: Literal["imported", "duplicate", "updated"]
    potential_duplicates: list[int]


class ReprocessOptions(BaseModel):
    refresh_parameters: bool = False
