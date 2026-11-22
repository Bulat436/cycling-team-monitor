from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .athlete_models import AthleteProfile, WellnessEntry
from .athlete_schemas import WellnessValue
from .sleep_models import SleepDay
from .sleep_schemas import ManualSleepEdit, SleepMove, SleepView

SleepSource = Literal["manual", "legacy", "automatic"]
TIME_FIELDS = {"start_time", "end_time", "start_offset_minutes", "end_offset_minutes"}
MANUAL_DATA_FIELDS = ("manual_minutes", "start_time", "end_time", "sleep_quality", "recovery")


@dataclass(frozen=True)
class SleepResolution:
    candidates: dict[SleepSource, int]
    source: SleepSource | None

    @property
    def minutes(self) -> int | None:
        return self.candidates[self.source] if self.source else None

    @property
    def conflict(self) -> bool:
        return len(set(self.candidates.values())) > 1


def resolve_sleep(
    manual: int | None, legacy: int | None, *, automatic: int | None = None,
    selected: SleepSource | None = None, cleared: bool = False,
) -> SleepResolution:
    """Resolve already-authorized candidates. The team path supplies manual and legacy only."""
    ordered: dict[SleepSource, int | None] = {"manual": manual, "legacy": legacy, "automatic": automatic}
    candidates = {source: minutes for source, minutes in ordered.items() if minutes is not None}
    source = None if cleared else selected if selected in candidates else next(iter(candidates), None)
    return SleepResolution(candidates, source)


def resolve_sleep_day(day: SleepDay | None, diary: WellnessEntry | None) -> SleepResolution:
    if day is None:
        return resolve_sleep(None, diary.sleep_minutes if diary else None)
    manual = day.manual_minutes if day.manual_minutes is not None else day.calculated_minutes
    return resolve_sleep(manual, day.legacy_minutes, selected=day.selected_source, cleared=day.duration_cleared)


def sleep_ratings(day: SleepDay | None, diary: WellnessEntry | None) -> tuple[int | None, int | None]:
    if diary is not None:
        return diary.sleep_quality, diary.recovery
    return (day.sleep_quality, day.recovery) if day else (None, None)


def sleep_view(profile: AthleteProfile, sleep_date: date, day: SleepDay | None, diary: WellnessEntry | None) -> SleepView:
    resolution = resolve_sleep_day(day, diary)
    quality, recovery = sleep_ratings(day, diary)
    override = day and day.manual_minutes is not None and day.calculated_minutes is not None and (
        day.manual_minutes != day.calculated_minutes
    )
    return SleepView(
        date=sleep_date, timezone=day.timezone if day else diary.timezone if diary else profile.timezone,
        version=day.version if day else 0, updated_at=day.updated_at if day else diary.updated_at if diary else None,
        duration_minutes=resolution.minutes, source=resolution.source, selected_source=day.selected_source if day else None,
        conflict=resolution.conflict, candidates=resolution.candidates,
        manually_corrected=resolution.source == "manual" and (bool(override) or resolution.conflict),
        start_time=day.start_time if day else None, end_time=day.end_time if day else None,
        start_offset_minutes=day.start_offset_minutes if day else None,
        end_offset_minutes=day.end_offset_minutes if day else None,
        entered_duration_minutes=day.manual_minutes if day else None,
        calculated_duration_minutes=day.calculated_minutes if day else None,
        sleep_quality=quality, recovery=recovery, has_diary=diary is not None,
    )


def new_sleep_day(profile: AthleteProfile, sleep_date: date, diary: WellnessEntry | None) -> SleepDay:
    return SleepDay(
        athlete_id=profile.athlete_id, sleep_date=sleep_date, timezone=diary.timezone if diary else profile.timezone,
        legacy_minutes=diary.sleep_minutes if diary else None, version=0, duration_cleared=False,
    )


def touch(day: SleepDay, timestamp: float):
    day.version += 1
    day.updated_at = timestamp


def local_instant(day: date, clock: str, zone: ZoneInfo, offset: int | None, field: str) -> tuple[datetime, int]:
    wall = datetime.combine(day, time.fromisoformat(clock))
    options = {}
    for fold in (0, 1):
        local = wall.replace(tzinfo=zone, fold=fold)
        try:
            utc = local.astimezone(UTC)
            if utc.astimezone(zone).replace(tzinfo=None) == wall:
                options[int(local.utcoffset().total_seconds() / 60)] = utc
        except (ValueError, OverflowError):
            pass
    if not options:
        raise HTTPException(422, {"code": "sleep_time_nonexistent", "field": field})
    if offset is not None and offset in options:
        return options[offset], offset
    if offset is None and len(options) == 1:
        return next((instant, minutes) for minutes, instant in options.items())
    code = "sleep_time_ambiguous" if offset is None else "sleep_offset_invalid"
    raise HTTPException(422, {"code": code, "field": field, "offset_minutes": sorted(options)})


def update_time_window(day: SleepDay, changes: dict):
    start = changes.get("start_time", day.start_time)
    end = changes.get("end_time", day.end_time)
    overnight = bool(start and end and start > end)
    was_overnight = bool(day.start_time and day.end_time and day.start_time > day.end_time)
    try:
        start_date = day.sleep_date - timedelta(days=overnight)
    except OverflowError:
        raise HTTPException(422, "sleep_date_invalid") from None
    zone = ZoneInfo(day.timezone)
    keep_start_offset = start == day.start_time and overnight == was_overnight
    start_offset = changes.get("start_offset_minutes", day.start_offset_minutes if keep_start_offset else None)
    end_offset = changes.get("end_offset_minutes", day.end_offset_minutes if end == day.end_time else None)
    instants = []
    for field, clock, local_date, offset in (("start", start, start_date, start_offset), ("end", end, day.sleep_date, end_offset)):
        if clock is None and offset is not None:
            raise HTTPException(422, {"code": "sleep_offset_without_time", "field": field + "_time"})
        instant, offset = local_instant(local_date, clock, zone, offset, field + "_time") if clock else (None, None)
        setattr(day, field + "_time", clock)
        setattr(day, field + "_offset_minutes", offset)
        instants.append(instant)
    day.calculated_minutes = None
    if all(instant is not None for instant in instants):
        seconds = (instants[1] - instants[0]).total_seconds()
        if not 0 <= seconds <= 1440 * 60:
            raise HTTPException(422, "sleep_window_invalid")
        day.calculated_minutes = int((seconds + 30) // 60)


def update_manual(day: SleepDay, diary: WellnessEntry | None, payload: ManualSleepEdit):
    changes = payload.model_dump(exclude_unset=True, exclude={"version"})
    times_changed = bool(TIME_FIELDS.intersection(changes))
    duration_changed = "duration_minutes" in changes
    if times_changed:
        update_time_window(day, changes)
    if duration_changed:
        day.manual_minutes = changes["duration_minutes"]
    has_duration = day.manual_minutes is not None or day.calculated_minutes is not None
    if times_changed or duration_changed:
        day.selected_source = None
        if duration_changed or has_duration:
            day.duration_cleared = not has_duration
    for field in ("sleep_quality", "recovery"):
        if field in changes:
            if diary is not None and field == "sleep_quality" and changes[field] is None:
                raise HTTPException(422, "diary_sleep_quality_required")
            setattr(diary if diary is not None else day, field, changes[field])


def has_manual_data(day: SleepDay) -> bool:
    return any(getattr(day, field) is not None for field in MANUAL_DATA_FIELDS)


def move_manual(source: SleepDay, destination: SleepDay, payload: SleepMove):
    destination.timezone = source.timezone
    times = payload.model_dump(exclude_unset=True, exclude={"version", "date"})
    update_time_window(destination, times | {"start_time": source.start_time, "end_time": source.end_time})
    destination.selected_source = None
    destination.duration_cleared = source.duration_cleared
    for field in ("manual_minutes", "sleep_quality", "recovery"):
        setattr(destination, field, getattr(source, field))
    for field in (*MANUAL_DATA_FIELDS, "calculated_minutes", "start_offset_minutes", "end_offset_minutes"):
        setattr(source, field, None)
    if source.selected_source == "manual":
        source.selected_source = None


def sync_legacy_write(
    db: Session, profile: AthleteProfile, sleep_date: date, diary: WellnessEntry | None,
    payload: WellnessValue, timestamp: float,
) -> tuple[int | None, int | None]:
    """Keep old diary clients usable, preserving the original duration and explicit choices."""
    day = db.get(SleepDay, (profile.athlete_id, sleep_date))
    minutes = resolve_sleep_day(day, diary).minutes
    previous_quality, previous_recovery = sleep_ratings(day, diary)
    recovery = payload.recovery if "recovery" in payload.model_fields_set else previous_recovery
    changed_duration = "sleep_minutes" in payload.model_fields_set and payload.sleep_minutes != minutes
    changed_ratings = (payload.sleep_quality, recovery) != (previous_quality, previous_recovery)
    transfer_ratings = diary is None and (previous_quality is not None or previous_recovery is not None)
    if changed_duration or changed_ratings or transfer_ratings:
        if day is None:
            day = new_sleep_day(profile, sleep_date, diary)
            db.add(day)
        if changed_duration:
            day.manual_minutes = payload.sleep_minutes
            day.duration_cleared = payload.sleep_minutes is None
            day.selected_source = None
        # Once the questionnaire exists its ratings are authoritative for this date.
        day.sleep_quality = None
        day.recovery = None
        touch(day, timestamp)
    return resolve_sleep_day(day, diary).minutes, recovery
