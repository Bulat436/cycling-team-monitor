from collections import Counter, defaultdict
from datetime import date, datetime, time as wall_time, timedelta
from math import fsum
from time import time
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .alert_rules import evaluate, load_settings, save_settings
from .analytics_schemas import (
    AlertCode, AlertSettings, AthleteAnalytics, DateRange, FtpPoint, LastWorkout, Load,
    TeamOverview, TeamRow, WeekPoint, WellnessPoint, WorkoutPoint, ZoneBand, ZoneDistribution,
)
from .athlete_models import FtpRecord, RpeEntry, WELLNESS_SCORES, WellnessEntry, Workout
from .athletes import AthleteScope, list_profiles, local_date
from .onelap_models import OnelapConnection
from .onelap_routes import connection_view
from .security import DB, Coach, audit
from .workout_models import WorkoutMetrics
from .workouts import rpe_view

router = APIRouter(prefix="/analytics", tags=["Аналитика"])
DAY = timedelta(days=1)
BASELINE_DAYS = 28


def range_filter(
    period: Literal["7", "28", "90", "custom"] = "7",
    start: date | None = None,
    end: date | None = None,
):
    if period == "custom":
        if start is None or end is None or end < start or (end - start).days >= 366:
            raise HTTPException(422, "invalid_analytics_range")
        if start.year < 1990 or end.year > 2200:
            raise HTTPException(422, "invalid_analytics_range")
        return DateRange(start=start, end=end)
    if start is not None or end is not None:
        raise HTTPException(422, "invalid_analytics_range")
    return int(period)


Period = Annotated[int | DateRange, Depends(range_filter)]


def select_range(period: int | DateRange, today: date) -> DateRange:
    if isinstance(period, DateRange):
        return period
    return DateRange(start=today - DAY * (period - 1), end=today)


def bounds(period: DateRange, timezone: str) -> tuple[int, int]:
    tz = ZoneInfo(timezone)
    return tuple(int(datetime.combine(day, wall_time.min, tz).timestamp()) for day in (period.start, period.end + DAY))


def workout_rows(db: Session, athlete_id: int, period: DateRange, timezone: str):
    lower, upper = bounds(period, timezone)
    return db.execute(
        select(Workout, RpeEntry, WorkoutMetrics.result).outerjoin(RpeEntry).outerjoin(WorkoutMetrics)
        .where(Workout.athlete_id == athlete_id, Workout.started_at >= lower, Workout.started_at < upper)
        .order_by(Workout.started_at, Workout.id)
    ).all()


def workout_point(workout, rpe, result, timezone):
    summary = workout.summary or {}
    calculated = result or {}
    effort = rpe_view(workout, rpe)
    values = {
        "timer_seconds": workout.timer_seconds, "elapsed_seconds": summary.get("elapsed_seconds"),
        "distance_m": summary.get("distance_m"), "avg_power": summary.get("avg_power"),
        "np": calculated.get("np"), "tss": calculated.get("tss"),
        "rpe": effort.score if effort else None, "rpe_load": effort.session_load if effort else None,
    }
    reasons = dict(calculated.get("reasons", {}))
    for field, value in values.items():
        if value is None and field not in reasons:
            reasons[field] = {
                "timer_seconds": "timer_missing", "rpe": "rpe_missing",
                "rpe_load": "rpe_missing" if effort is None else "timer_missing",
            }.get(field, "not_recorded")
    return WorkoutPoint(
        id=workout.id, date=local_date(timezone, workout.started_at), started_at=workout.started_at,
        **values, reasons=reasons,
    )


def sum_known(values):
    known = [value for value in values if value is not None]
    return fsum(known) if known else None


def load(points: list[WorkoutPoint]) -> Load:
    return Load(
        workouts=len(points), timer_seconds=sum_known(point.timer_seconds for point in points),
        distance_m=sum_known(point.distance_m for point in points),
        tss=sum_known(point.tss for point in points), rpe_load=sum_known(point.rpe_load for point in points),
        tss_count=sum(point.tss is not None for point in points),
        rpe_count=sum(point.rpe is not None for point in points),
        rpe_load_count=sum(point.rpe_load is not None for point in points),
    )


def weekly_load(points: list[WorkoutPoint], period: DateRange) -> list[WeekPoint]:
    groups = defaultdict(list)
    for point in points:
        groups[point.date - DAY * point.date.weekday()].append(point)
    monday = period.start - DAY * period.start.weekday()
    weeks = []
    while monday <= period.end:
        week_load = load(groups[monday])
        weeks.append(WeekPoint(
            start=max(period.start, monday), end=min(period.end, monday + DAY * 6), **week_load.model_dump(),
        ))
        monday += 7 * DAY
    return weeks


def zone_distribution(rows, key) -> ZoneDistribution:
    totals = Counter()
    omitted = Counter()
    outside = unknown = measured = 0
    for _, _, result in rows:
        value = result.get(key) if result else None
        if value is None:
            omitted[(result or {}).get("reasons", {}).get(key, "not_recorded")] += 1
            continue
        measured += 1
        outside += value["outside_seconds"]
        unknown += value["unknown_seconds"]
        for zone in value["zones"]:
            totals[(zone["name"], zone["minimum"], zone["maximum"])] += zone["seconds"]
    bands = [
        ZoneBand(name=name, minimum=minimum, maximum=maximum, seconds=seconds)
        for (name, minimum, maximum), seconds in sorted(
            totals.items(), key=lambda row: (row[0][1], row[0][2] if row[0][2] is not None else float("inf"), row[0][0]),
        )
    ]
    return ZoneDistribution(
        bands=bands, outside_seconds=outside if measured else None, unknown_seconds=unknown if measured else None,
        workouts_with_data=measured, workouts=len(rows), omitted=dict(omitted),
    )


def wellness_points(db: Session, athlete_id: int, period: DateRange):
    entries = db.scalars(select(WellnessEntry).where(
        WellnessEntry.athlete_id == athlete_id, WellnessEntry.entry_date <= period.end,
        WellnessEntry.entry_date >= period.start - DAY * BASELINE_DAYS,
    )).all()
    available = {entry.entry_date: {key: getattr(entry, key) for key in WELLNESS_SCORES} for entry in entries}
    result = []
    for offset in range((period.end - period.start).days + 1):
        day = period.start + DAY * offset
        previous = [available[earlier] for offset in range(1, BASELINE_DAYS + 1)
                    if (earlier := day - DAY * offset) in available]
        baseline = ({key: fsum(row[key] for row in previous) / len(previous) for key in WELLNESS_SCORES}
                    if len(previous) >= 3 else None)
        result.append(WellnessPoint(date=day, scores=available.get(day), baseline=baseline))
    return result


def ftp_points(db: Session, athlete_id: int, period: DateRange):
    relevant = FtpRecord.athlete_id == athlete_id
    leading = db.scalar(select(FtpRecord).where(relevant, FtpRecord.effective_from < period.start)
                        .order_by(FtpRecord.effective_from.desc()).limit(1))
    records = db.scalars(select(FtpRecord).where(
        relevant, FtpRecord.effective_from.between(period.start, period.end),
    ).order_by(FtpRecord.effective_from)).all()
    points = [FtpPoint(date=row.effective_from, watts=row.watts, effective_from=row.effective_from) for row in records]
    if leading:
        points.insert(0, FtpPoint(date=period.start, watts=leading.watts, effective_from=leading.effective_from))
    if points and period.end > points[-1].date:
        points.append(points[-1].model_copy(update={"date": period.end}))
    return points


def row_summary(db: Session, profile, user, selected: int | DateRange, config, onelap, timestamp: float) -> TeamRow:
    timezone = profile.timezone
    local_now = datetime.fromtimestamp(timestamp, ZoneInfo(timezone))
    today = local_now.date()
    period = select_range(selected, today)
    diary = db.get(WellnessEntry, (user.id, today))
    latest = db.execute(
        select(Workout, RpeEntry.score).outerjoin(RpeEntry)
        .where(Workout.athlete_id == user.id, Workout.started_at <= timestamp)
        .order_by(Workout.started_at.desc(), Workout.id.desc()).limit(1)
    ).first()
    missing_rpe = db.scalar(
        select(func.count()).select_from(Workout).outerjoin(RpeEntry)
        .where(
            Workout.athlete_id == user.id,
            Workout.started_at >= timestamp - 86400 * config.rpe_lookback_days,
            Workout.started_at + func.coalesce(
                Workout.summary["elapsed_seconds"].as_float(), Workout.timer_seconds, 0,
            ) <= timestamp - 3600 * config.rpe_grace_hours,
            RpeEntry.workout_id.is_(None),
        )
    )
    sync = connection_view(onelap, db.get(OnelapConnection, user.id))
    workout = latest[0] if latest else None
    return TeamRow(
        athlete_id=user.id, name=user.name, group=profile.team_group, today=today, timezone=timezone,
        account="inactive" if not user.active else (
            "invited" if user.activated_at is None else "password_reset" if user.password_pending else "active"
        ),
        period=period, wellness=diary, load=load([workout_point(*row, timezone) for row in workout_rows(db, user.id, period, timezone)]),
        onelap=sync, alerts=evaluate(config, local_now, user, diary, workout, missing_rpe, sync),
        last_workout=LastWorkout(
            id=workout.id, started_at=workout.started_at, timer_seconds=workout.timer_seconds,
            distance_m=(workout.summary or {}).get("distance_m"), rpe=latest[1],
        ) if workout else None,
    )


@router.get("/team", response_model=TeamOverview)
def team(
    db: DB, coach: Coach, request: Request, period: Period,
    search: Annotated[str, Query(max_length=120)] = "",
    group: Annotated[str | None, Query(max_length=120)] = None,
    account: Literal["active", "inactive", "pending"] | None = None,
    diary: Literal["filled", "missing"] | None = None,
    warning: AlertCode | Literal["any"] | None = None,
    sort: Literal["name", "last_workout", "tss", "rpe_load", "alerts"] = "name",
    direction: Literal["asc", "desc"] = "asc",
):
    config = load_settings(db)
    timestamp = time()
    rows = [row_summary(db, profile, user, period, config, request.app.state.onelap, timestamp)
            for profile, user in list_profiles(db)]
    needle = search.strip().casefold()
    filtered = [
        row for row in rows
        if needle in row.name.casefold() and (group is None or group == row.group)
        and (account is None or ("pending" if row.account in {"invited", "password_reset"} else row.account) == account)
        and (diary is None or (row.wellness is not None) == (diary == "filled"))
        and (warning is None or (bool(row.alerts) if warning == "any" else any(item.code == warning for item in row.alerts)))
    ]
    filtered.sort(key=lambda row: (row.name.casefold(), row.athlete_id))
    keys = {
        "name": lambda row: row.name.casefold(),
        "last_workout": lambda row: row.last_workout.started_at if row.last_workout else None,
        "tss": lambda row: row.load.tss, "rpe_load": lambda row: row.load.rpe_load,
        "alerts": lambda row: len(row.alerts),
    }
    key = keys[sort]
    ordered = sorted((row for row in filtered if key(row) is not None), key=key, reverse=direction == "desc")
    ordered.extend(row for row in filtered if key(row) is None)
    return TeamOverview(
        total=len(rows), active=sum(row.account == "active" for row in rows),
        pending=sum(row.account in {"invited", "password_reset"} for row in rows),
        filled=sum(row.wellness is not None for row in rows), alerted=sum(bool(row.alerts) for row in rows),
        groups=sorted({row.group for row in rows if row.group}), rows=ordered,
    )


@router.get("/athletes/{athlete_id}/overview", response_model=TeamRow)
def overview(scope: AthleteScope, db: DB, coach: Coach, request: Request, period: Period):
    return row_summary(db, *scope, period, load_settings(db), request.app.state.onelap, time())


@router.get("/athletes/{athlete_id}", response_model=AthleteAnalytics)
def athlete_analytics(scope: AthleteScope, db: DB, coach: Coach, period: Period):
    profile, _ = scope
    timezone = profile.timezone
    selected = select_range(period, local_date(timezone, time()))
    rows = workout_rows(db, profile.athlete_id, selected, timezone)
    points = [workout_point(*row, timezone) for row in rows]
    return AthleteAnalytics(
        period=selected, timezone=timezone, load=load(points),
        wellness=wellness_points(db, profile.athlete_id, selected),
        workouts=points, weeks=weekly_load(points, selected),
        ftp=ftp_points(db, profile.athlete_id, selected),
        power_zones=zone_distribution(rows, "power_zones"),
        heart_rate_zones=zone_distribution(rows, "heart_rate_zones"),
    )


@router.get("/alert-settings", response_model=AlertSettings)
def get_alerts(db: DB, coach: Coach):
    return load_settings(db)


@router.put("/alert-settings", response_model=AlertSettings)
def put_alerts(payload: AlertSettings, db: DB, coach: Coach):
    save_settings(db, payload)
    audit(db, "alert_settings_updated", coach.id)
    db.commit()
    return payload
