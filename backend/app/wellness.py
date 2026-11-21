from datetime import date
from time import time
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from .athlete_models import WellnessEntry
from .athlete_schemas import TeamWellness, WellnessEntryView, WellnessHistory, WellnessValue
from .athletes import AthleteScope, list_profiles, local_date, profile_summary
from .security import DB, Auth, Coach

router = APIRouter(tags=["Утренний дневник"])


@router.get("/athletes/{athlete_id}/wellness", response_model=WellnessHistory)
def wellness_history(
    scope: AthleteScope, db: DB,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
):
    profile, _ = scope
    today = local_date(profile.timezone)
    entries = db.scalars(
        select(WellnessEntry).where(WellnessEntry.athlete_id == profile.athlete_id)
        .order_by(WellnessEntry.entry_date.desc()).offset(offset).limit(limit + 1)
    ).all()
    return {
        "timezone": profile.timezone, "local_date": today,
        "today_entry": db.get(WellnessEntry, (profile.athlete_id, today)),
        "entries": entries[:limit], "has_more": len(entries) > limit,
    }


@router.get("/athletes/{athlete_id}/wellness/{entry_date}", response_model=WellnessEntryView | None)
def daily_entry(entry_date: date, scope: AthleteScope, db: DB):
    return db.get(WellnessEntry, (scope[0].athlete_id, entry_date))


@router.put("/athletes/{athlete_id}/wellness/{entry_date}", response_model=WellnessEntryView)
def save_daily_entry(payload: WellnessValue, entry_date: date, scope: AthleteScope, auth: Auth, db: DB):
    profile, _ = scope
    if auth.user.role != "athlete":
        raise HTTPException(403, "role_denied")
    timestamp = time()
    today = local_date(profile.timezone, timestamp)
    if payload.training_day_status is not None and entry_date >= today:
        raise HTTPException(422, "training_day_not_completed")
    entry = db.get(WellnessEntry, (profile.athlete_id, entry_date))
    if entry is None:
        if entry_date > today:
            raise HTTPException(422, "future_diary_date")
        entry = WellnessEntry(
            athlete_id=profile.athlete_id, entry_date=entry_date,
            timezone=profile.timezone, created_at=timestamp,
        )
        db.add(entry)
    values = payload.model_dump()
    for optional in ("sleep_minutes", "recovery", "training_day_status"):
        if optional not in payload.model_fields_set:
            values.pop(optional)
    for field, value in values.items():
        setattr(entry, field, value)
    entry.updated_at = timestamp
    db.commit()
    return entry


@router.get("/wellness/team", response_model=list[TeamWellness])
def team_wellness(db: DB, coach: Coach):
    timestamp = time()
    profiles = [(profile, user, local_date(profile.timezone, timestamp)) for profile, user in list_profiles(db)]
    dates = {today for _, _, today in profiles}
    entries = db.scalars(select(WellnessEntry).where(WellnessEntry.entry_date.in_(dates))).all()
    by_athlete_and_date = {(entry.athlete_id, entry.entry_date): entry for entry in entries}
    return [
        TeamWellness(
            **profile_summary(profile, user).model_dump(), local_date=today,
            entry=by_athlete_and_date.get((profile.athlete_id, today)),
        )
        for profile, user, today in profiles
    ]
