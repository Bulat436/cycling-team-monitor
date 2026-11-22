from datetime import date
from time import time
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import and_, or_, select, union

from .athlete_models import WellnessEntry
from .athletes import AthleteScope, local_date
from .security import Auth, DB
from .sleep import MANUAL_DATA_FIELDS, has_manual_data, move_manual, new_sleep_day, resolve_sleep_day, sleep_view, touch, update_manual
from .sleep_models import SleepDay
from .sleep_schemas import ManualSleepEdit, SleepHistory, SleepMove, SleepSelection, SleepView

router = APIRouter(prefix="/athletes/{athlete_id}/sleep", tags=["Сон"])


@router.get("", response_model=SleepHistory)
def history(
    scope: AthleteScope, db: DB, offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
):
    profile, _ = scope
    athlete_id = profile.athlete_id
    days = union(
        select(SleepDay.sleep_date.label("day")).where(
            SleepDay.athlete_id == athlete_id,
            or_(SleepDay.legacy_minutes.is_not(None), *(getattr(SleepDay, field).is_not(None) for field in MANUAL_DATA_FIELDS)),
        ),
        select(WellnessEntry.entry_date).where(WellnessEntry.athlete_id == athlete_id),
    ).subquery()
    rows = db.execute(select(days.c.day, SleepDay, WellnessEntry).select_from(days)
                      .outerjoin(SleepDay, and_(SleepDay.athlete_id == athlete_id, SleepDay.sleep_date == days.c.day))
                      .outerjoin(WellnessEntry, and_(WellnessEntry.athlete_id == athlete_id, WellnessEntry.entry_date == days.c.day))
                      .order_by(days.c.day.desc()).offset(offset).limit(limit + 1)).all()
    return SleepHistory(
        timezone=profile.timezone, local_date=local_date(profile.timezone, time()), has_more=len(rows) > limit,
        entries=[sleep_view(profile, day, record, diary) for day, record, diary in rows[:limit]],
    )


@router.get("/{sleep_date}", response_model=SleepView)
def read(sleep_date: date, scope: AthleteScope, db: DB):
    profile, _ = scope
    key = (profile.athlete_id, sleep_date)
    return sleep_view(profile, sleep_date, db.get(SleepDay, key), db.get(WellnessEntry, key))


def editable(sleep_date: date, version: int, scope: AthleteScope, auth: Auth, db: DB):
    profile, _ = scope
    if auth.user.role != "athlete":
        raise HTTPException(403, "role_denied")
    timestamp = time()
    key = (profile.athlete_id, sleep_date)
    day, diary = db.get(SleepDay, key), db.get(WellnessEntry, key)
    if day is None and diary is None and sleep_date > local_date(profile.timezone, timestamp):
        raise HTTPException(422, "future_sleep_date")
    if version != (day.version if day else 0):
        raise HTTPException(409, "sleep_version_conflict")
    return profile, day, diary, timestamp


def update_projection(db: DB, day: SleepDay, diary: WellnessEntry | None, timestamp: float):
    touch(day, timestamp)
    if diary is not None:
        diary.sleep_minutes = resolve_sleep_day(day, diary).minutes
        if db.is_modified(diary):
            diary.updated_at = timestamp


@router.patch("/{sleep_date}/manual", response_model=SleepView)
def manual(payload: ManualSleepEdit, sleep_date: date, scope: AthleteScope, auth: Auth, db: DB):
    profile, day, diary, timestamp = editable(sleep_date, payload.version, scope, auth, db)
    if payload.model_fields_set != {"version"}:
        if day is None:
            day = new_sleep_day(profile, sleep_date, diary)
            db.add(day)
        update_manual(day, diary, payload)
        update_projection(db, day, diary, timestamp)
        db.commit()
    return sleep_view(profile, sleep_date, day, diary)


@router.put("/{sleep_date}/selection", response_model=SleepView)
def selection(payload: SleepSelection, sleep_date: date, scope: AthleteScope, auth: Auth, db: DB):
    profile, day, diary, timestamp = editable(sleep_date, payload.version, scope, auth, db)
    choices = resolve_sleep_day(day, diary).candidates
    if payload.source is not None and payload.source not in choices:
        raise HTTPException(422, "sleep_source_unavailable")
    if day is not None or payload.source is not None:
        if day is None:
            day = new_sleep_day(profile, sleep_date, diary)
            db.add(day)
        day.selected_source = payload.source
        if payload.source is not None:
            day.duration_cleared = False
        update_projection(db, day, diary, timestamp)
        db.commit()
    return sleep_view(profile, sleep_date, day, diary)


@router.post("/{sleep_date}/move", response_model=SleepView)
def move(payload: SleepMove, sleep_date: date, scope: AthleteScope, auth: Auth, db: DB):
    profile, day, diary, timestamp = editable(sleep_date, payload.version, scope, auth, db)
    if day is None or not has_manual_data(day):
        raise HTTPException(422, "sleep_manual_unavailable")
    if payload.date == sleep_date or payload.date > local_date(profile.timezone, timestamp):
        raise HTTPException(422, "sleep_date_invalid")
    key = (profile.athlete_id, payload.date)
    destination = db.get(SleepDay, key)
    if db.get(WellnessEntry, key) is not None or (destination and (destination.legacy_minutes is not None or has_manual_data(destination))):
        raise HTTPException(409, "sleep_date_occupied")
    if destination is None:
        destination = new_sleep_day(profile, payload.date, None)
        db.add(destination)
    move_manual(day, destination, payload)
    update_projection(db, day, diary, timestamp)
    touch(destination, timestamp)
    db.commit()
    return sleep_view(profile, payload.date, destination, None)
