from itertools import chain
from time import time
from typing import Literal

from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import Session

from ..analytics_models import AppSetting
from ..athlete_models import AthleteProfile, Workout
from ..athletes import AthleteScope, list_profiles
from ..security import DB, Auth, Coach, audit
from ..workout_metrics import stored_active_observations
from ..workout_models import WorkoutEffort, WorkoutMetrics
from ..workouts import WorkoutScope, read_sample_blocks
from .load import DAY
from .performance import effort_view
from .power import POWER_PROJECTION_VERSION, best_power, interval_power
from .schemas import EffortValue, EffortView, Measurements, Rules
from .service import SETTINGS_KEY, load_rules, measurements, warning_measurements
from .warning_schemas import AthleteWarnings, OwnRecovery, TeamWarningRow, TeamWarnings
from .warnings import ATTENTION_ORDER, athlete_warnings

router = APIRouter(tags=["Нагрузка и восстановление"])


@router.get("/training-intelligence/athletes/{athlete_id}/measurements", response_model=Measurements)
def athlete_measurements(scope: AthleteScope, db: DB, coach: Coach, period: Literal["7", "28", "42", "90"] = "28"):
    return measurements(db, scope[0], int(period), time(), load_rules(db))


@router.get("/training-intelligence/athletes/{athlete_id}/warnings", response_model=AthleteWarnings)
def warnings_for_athlete(scope: AthleteScope, db: DB, coach: Coach):
    data = next(warning_measurements(db, [scope[0]], time(), load_rules(db)))
    return athlete_warnings(data)


@router.get("/training-intelligence/team", response_model=TeamWarnings)
def team_warning_overview(db: DB, coach: Coach):
    profiles = list_profiles(db)
    members = {profile.athlete_id: (profile, user) for profile, user in profiles}
    rows = []
    for data in warning_measurements(db, [profile for profile, _ in profiles], time(), load_rules(db)):
        profile, user = members[data.athlete_id]
        rows.append(TeamWarningRow(
            **athlete_warnings(data).model_dump(), name=user.name, group=profile.team_group, active=user.active,
        ))
    rows.sort(key=lambda row: (-ATTENTION_ORDER[row.max_attention], row.name.casefold(), row.athlete_id))
    return TeamWarnings(athletes=rows)


@router.get("/training-intelligence/me", response_model=OwnRecovery)
def own_recovery(db: DB, auth: Auth, period: Literal["28"] | None = None):
    if auth.user.role != "athlete":
        raise HTTPException(403, "role_denied")
    profile = db.get(AthleteProfile, auth.user.id)
    if profile is None:
        raise HTTPException(404, "athlete_missing")
    rules = load_rules(db)
    data = next(warning_measurements(db, [profile], time(), rules, minimum_days=int(period or 0)))
    start = data.period.end - (int(period) - 1) * DAY if period else data.current_week.period.start
    return OwnRecovery(
        athlete_id=profile.athlete_id, timezone=profile.timezone, completed_through=data.completed_through,
        load=data.current_week, recovery=[day for day in data.recovery if day.date >= start],
        missing_diary_dates=[day for day in data.missing_diary_dates if day >= start],
        recovery_window_days=rules.recovery_window_days,
    )


@router.get("/training-intelligence/settings", response_model=Rules)
def get_rules(db: DB, coach: Coach):
    return load_rules(db)


@router.put("/training-intelligence/settings", response_model=Rules)
def save_rules(payload: Rules, db: DB, coach: Coach):
    row = db.get(AppSetting, SETTINGS_KEY)
    if row is None:
        row = AppSetting(key=SETTINGS_KEY)
        db.add(row)
    row.value = payload.model_dump()
    audit(db, "training_intelligence_settings_updated", coach.id)
    db.commit()
    return payload


def cycling(workout: Workout) -> bool:
    return set((workout.summary or {}).get("sports") or ()) <= {"cycling"}


def observations(db: Session, workout: Workout, metrics: WorkoutMetrics):
    return stored_active_observations(
        chain.from_iterable(read_sample_blocks(db, workout.id)),
        metrics.metadata_json.get("timer_events", []), workout.started_at,
        (workout.summary or {}).get("elapsed_seconds"), workout.timer_seconds,
    )


@router.get("/training-intelligence/workouts/{workout_id}/best-power", response_model=dict[str, float | None])
def workout_best_power(workout: WorkoutScope, db: DB, coach: Coach):
    metrics = db.get(WorkoutMetrics, workout.id)
    if metrics is None or not cycling(workout):
        return best_power(())
    projection = metrics.result.get("power_projection", {})
    if projection.get("version") == POWER_PROJECTION_VERSION:
        return projection["best_watts"]
    return best_power(observations(db, workout, metrics))


@router.get("/training-intelligence/workouts/{workout_id}/effort", response_model=EffortView | None)
def get_effort(workout: WorkoutScope, db: DB, coach: Coach):
    effort = db.get(WorkoutEffort, workout.id)
    return effort_view(workout, effort) if effort is not None else None


@router.put("/training-intelligence/workouts/{workout_id}/effort", response_model=EffortView)
def save_effort(payload: EffortValue, workout: WorkoutScope, db: DB, coach: Coach):
    metrics = db.get(WorkoutMetrics, workout.id)
    if metrics is None or workout.file_sha256 is None or not cycling(workout):
        raise HTTPException(422, "fit_power_unavailable")
    start = workout.started_at + payload.start_seconds
    watts = interval_power(observations(db, workout, metrics), start, payload.duration_seconds)
    if watts is None:
        raise HTTPException(422, "incomplete_effort_power")
    effort = db.get(WorkoutEffort, workout.id)
    if effort is None:
        effort = WorkoutEffort(workout_id=workout.id)
        db.add(effort)
    effort.protocol = payload.protocol
    effort.start_seconds = payload.start_seconds
    effort.duration_seconds = payload.duration_seconds
    effort.file_sha256 = workout.file_sha256
    effort.projection_version = POWER_PROJECTION_VERSION
    effort.mean_watts = watts
    audit(db, "workout_effort_saved", coach.id, workout.athlete_id)
    db.commit()
    return effort_view(workout, effort)


@router.delete("/training-intelligence/workouts/{workout_id}/effort", status_code=204)
def delete_effort(workout: WorkoutScope, db: DB, coach: Coach):
    effort = db.get(WorkoutEffort, workout.id)
    if effort is not None:
        db.delete(effort)
        audit(db, "workout_effort_deleted", coach.id, workout.athlete_id)
        db.commit()
