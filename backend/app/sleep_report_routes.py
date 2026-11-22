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
from .sleep_models import PrivateSleepReportNight
from .sleep_report_schemas import MAX_SLEEP_REPORT_BYTES, SleepReport, SleepReportHistory, SleepReportNight, SleepReportResult

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


async def read_report(request: Request) -> SleepReport:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "sleep_report_content_type")
    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_SLEEP_REPORT_BYTES:
                raise HTTPException(413, "sleep_report_too_large")
            body.extend(chunk)
        data = json.loads(body.decode("utf-8"), object_pairs_hook=unique_fields)
    except (ValueError, RecursionError, ClientDisconnect):
        raise HTTPException(422, "sleep_report_invalid") from None

    try:
        report = SleepReport.model_validate(data)
    except ValidationError as exc:
        for error in exc.errors(include_input=False, include_context=False, include_url=False):
            location = error["loc"]
            if len(location) > 1 and location[0] == "sleep" and isinstance(location[1], int):
                raise HTTPException(422, {"code": "sleep_report_night_invalid", "field": str(location[1] + 1)}) from None
        raise HTTPException(422, "sleep_report_invalid") from None

    if len({night.sleep_date for night in report.sleep}) != len(report.sleep):
        raise HTTPException(422, "sleep_report_duplicate_date")
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


@router.post("/preview", response_model=SleepReport)
async def preview(athlete_id: int, request: Request, actor: Actor):
    require_owner(athlete_id, actor.user_id, actor.role)
    return await read_report(request)


def store_report(athlete_id: int, request: Request, report: SleepReport) -> SleepReportResult:
    with Session(request.app.state.engine) as db:
        db.execute(text("BEGIN IMMEDIATE"))
        # Recheck the session after the upload, before writing any health data.
        auth = authenticate(request, db)
        require_owner(athlete_id, auth.user.id, auth.user.role)
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
            if row is None:
                db.add(PrivateSleepReportNight(
                    athlete_id=athlete_id, sleep_date=night.sleep_date, details=details, imported_at=timestamp,
                ))
            elif row.details != details:
                row.details = details
                row.imported_at = timestamp
            else:
                continue
            saved += 1
        db.commit()
    return SleepReportResult(saved=saved, unchanged=len(report.sleep) - saved)


@router.put("", response_model=SleepReportResult)
async def save(athlete_id: int, request: Request, actor: Actor):
    require_owner(athlete_id, actor.user_id, actor.role)
    report = await read_report(request)
    return await run_in_threadpool(store_report, athlete_id, request, report)
