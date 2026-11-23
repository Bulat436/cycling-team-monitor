import csv
import json
from time import time
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from .security import Actor, Auth, DB, authenticate
from .athlete_models import AthleteProfile, WellnessEntry
from .sleep import TIME_FIELDS, new_sleep_day
from .sleep_models import PrivateSleepReportNight, SleepDay
from .sleep_routes import update_projection
from .sleep_report_schemas import (MAX_SLEEP_REPORT_BYTES, ActivityReport, ActivityReportResult, SleepReport,
                                   SleepReportHistory, SleepReportNight, SleepReportResult, csv_report)
from .workout_report import store_workout

router = APIRouter(prefix="/athletes/{athlete_id}/sleep-reports", tags=["Личные отчёты о сне"])


def require_owner(athlete_id: int, user_id: int, role: str):
    if role != "athlete":
        raise HTTPException(403, "role_denied")
    if athlete_id != user_id:
        raise HTTPException(404, "athlete_missing")


def unique_fields(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate JSON keys")
    return result


async def read_report(request: Request) -> SleepReport | ActivityReport:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {"application/json", "text/csv"}:
        raise HTTPException(415, "sleep_report_content_type")
    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_SLEEP_REPORT_BYTES:
                raise HTTPException(413, "sleep_report_too_large")
            body.extend(chunk)
        data = (csv_report(body.decode("utf-8-sig")) if content_type == "text/csv"
                else json.loads(body.decode("utf-8"), object_pairs_hook=unique_fields))
    except (ValueError, RecursionError, ClientDisconnect, csv.Error):
        raise HTTPException(422, "sleep_report_invalid") from None

    try:
        schema = ActivityReport if isinstance(data, dict) and data.get("schema_version") == 2 else SleepReport
        report = schema.model_validate(data)
    except ValidationError as exc:
        for error in exc.errors(include_input=False, include_context=False, include_url=False):
            location = error["loc"]
            if len(location) > 1 and location[0] == "sleep" and isinstance(location[1], int):
                raise HTTPException(422, {"code": "sleep_report_night_invalid", "field": str(location[1] + 1)}) from None
            if len(location) > 1 and location[0] == "workouts" and isinstance(location[1], int):
                raise HTTPException(422, {"code": "report_workout_invalid", "field": str(location[1] + 1)}) from None
        raise HTTPException(422, "sleep_report_invalid") from None

    if len({night.sleep_date for night in report.sleep}) != len(report.sleep):
        raise HTTPException(422, "sleep_report_duplicate_date")
    if isinstance(report, ActivityReport) and len({item.started_at for item in report.workouts}) != len(report.workouts):
        raise HTTPException(422, "report_workout_duplicate")
    return report


@router.get("", response_model=SleepReportHistory)
def history(
    athlete_id: int, auth: Auth, db: DB, offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
):
    require_owner(athlete_id, auth.user.id, auth.user.role)
    rows = db.scalars(select(PrivateSleepReportNight).where(PrivateSleepReportNight.athlete_id == athlete_id)
                      .order_by(PrivateSleepReportNight.sleep_date.desc()).offset(offset).limit(limit + 1)).all()
    return SleepReportHistory(
        entries=[SleepReportNight(sleep_date=row.sleep_date.isoformat(), **row.details) for row in rows[:limit]],
        has_more=len(rows) > limit,
    )


@router.post("/preview", response_model=SleepReport | ActivityReport)
async def preview(athlete_id: int, request: Request, actor: Actor):
    require_owner(athlete_id, actor.user_id, actor.role)
    return await read_report(request)


def apply_report_duration(db, profile, day, night, timestamp):
    duration = night.duration_minutes
    if day is None and duration is None:
        return False
    key = (profile.athlete_id, night.sleep_date)
    diary = db.get(WellnessEntry, key)
    if day is None:
        day = new_sleep_day(profile, night.sleep_date, diary)
        db.add(day)
    manual = day.manual_minutes if day.manual_minutes is not None else day.calculated_minutes
    conflict = duration is not None and manual is not None and duration != manual
    for prefix, instant in (("start", night.start_at), ("end", night.end_at)):
        clock = getattr(day, f"{prefix}_time")
        offset = getattr(day, f"{prefix}_offset_minutes")
        if instant is not None and clock is not None:
            conflict |= clock != instant.strftime("%H:%M") or (
                offset is not None and offset != instant.utcoffset().total_seconds() / 60
            )
    if conflict and duration is None:
        # With only timestamps, keep an independently entered duration.
        for field in TIME_FIELDS:
            setattr(day, field, None)
        day.calculated_minutes = None
    elif conflict:
        for field in (*TIME_FIELDS, "manual_minutes", "calculated_minutes"):
            setattr(day, field, None)
    selection_changed = duration is not None and (day.selected_source != "automatic" or day.duration_cleared)
    if not (conflict or selection_changed or day.report_minutes != duration):
        return False
    day.report_minutes = duration
    if duration is not None:
        day.selected_source = "automatic"
        day.duration_cleared = False
    elif day.selected_source == "automatic":
        day.selected_source = None
    update_projection(db, day, diary, timestamp)
    return True


def store_report(athlete_id: int, request: Request, report: SleepReport | ActivityReport) -> SleepReportResult:
    with Session(request.app.state.engine) as db:
        db.execute(text("BEGIN IMMEDIATE"))
        # Recheck the session after the upload, before writing any health data.
        auth = authenticate(request, db)
        require_owner(athlete_id, auth.user.id, auth.user.role)
        profile = db.get(AthleteProfile, athlete_id)
        if profile is None:
            raise HTTPException(404, "athlete_missing")
        rows = db.scalars(select(PrivateSleepReportNight).where(
            PrivateSleepReportNight.athlete_id == athlete_id,
            PrivateSleepReportNight.sleep_date.in_([night.sleep_date for night in report.sleep]),
        ))
        by_date = {row.sleep_date: row for row in rows}
        timestamp = time()
        saved = 0
        for night in report.sleep:
            row = by_date.get(night.sleep_date)
            details = night.details()
            key = (athlete_id, night.sleep_date)
            day = db.get(SleepDay, key)
            projection_changed = apply_report_duration(db, profile, day, night, timestamp)
            if row is None:
                db.add(PrivateSleepReportNight(
                    athlete_id=athlete_id, sleep_date=night.sleep_date, details=details, imported_at=timestamp, team_visible=True,
                ))
            elif row.details != details or not row.team_visible:
                row.details = details
                row.imported_at = timestamp
                row.team_visible = True
            elif not projection_changed:
                continue
            saved += 1
        if isinstance(report, ActivityReport):
            changed = sum(store_workout(db, request.app.state.workout_import, profile, item) for item in report.workouts)
            db.commit()
            return ActivityReportResult(saved=saved, unchanged=len(report.sleep) - saved,
                                        workouts_saved=changed, workouts_unchanged=len(report.workouts) - changed)
        db.commit()
    return SleepReportResult(saved=saved, unchanged=len(report.sleep) - saved)


@router.put("", response_model=ActivityReportResult | SleepReportResult)
async def save(athlete_id: int, request: Request, actor: Actor, confirm: str | None = None):
    require_owner(athlete_id, actor.user_id, actor.role)
    report = await read_report(request)
    if confirm != "share-and-replace":
        raise HTTPException(422, "report_confirmation_required")
    return await run_in_threadpool(store_report, athlete_id, request, report)
