from itertools import chain
from time import time
from typing import Literal

from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import Session

from ..analytics_models import AppSetting
from ..athlete_models import Workout
from ..athletes import AthleteScope
from ..security import DB, Coach, audit
from ..workout_metrics import stored_active_observations
from ..workout_models import WorkoutEffort, WorkoutMetrics
from ..workouts import WorkoutScope, read_sample_blocks
from .performance import effort_view
from .power import POWER_PROJECTION_VERSION, best_power, interval_power
from .schemas import EffortValue, EffortView, Measurements, Rules
from .service import SETTINGS_KEY, load_rules, measurements

router = APIRouter(tags=["Нагрузка и восстановление"])


@router.get("/training-intelligence/athletes/{athlete_id}/measurements", response_model=Measurements)
def athlete_measurements(scope: AthleteScope, db: DB, coach: Coach, period: Literal["7", "28", "42", "90"] = "28"):
    return measurements(db, scope[0], int(period), time(), load_rules(db))


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
