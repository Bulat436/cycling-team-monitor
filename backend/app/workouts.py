from time import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from .athlete_models import RpeEntry, Workout
from .athlete_schemas import RpeValue, RpeView, WorkoutList, WorkoutView
from .athletes import AthleteScope
from .security import DB, Auth

router = APIRouter(tags=["Тренировки и RPE"])


def workout_scope(workout_id: int, db: DB, auth: Auth) -> Workout:
    workout = db.get(Workout, workout_id)
    if not workout or (auth.user.role != "coach" and workout.athlete_id != auth.user.id):
        raise HTTPException(404, "workout_missing")
    return workout


WorkoutScope = Annotated[Workout, Depends(workout_scope)]


def rpe_view(workout: Workout, rpe: RpeEntry | None) -> RpeView | None:
    if rpe is None:
        return None
    return RpeView(
        workout_id=workout.id, score=rpe.score, comment=rpe.comment,
        session_load=None if workout.timer_seconds is None else workout.timer_seconds / 60 * rpe.score,
        created_at=rpe.created_at, updated_at=rpe.updated_at,
    )


def workout_view(workout: Workout, rpe: RpeEntry | None) -> WorkoutView:
    return WorkoutView(
        id=workout.id, athlete_id=workout.athlete_id, started_at=workout.started_at,
        timer_seconds=workout.timer_seconds, rpe=rpe_view(workout, rpe),
    )


@router.get("/athletes/{athlete_id}/workouts", response_model=WorkoutList)
def list_workouts(
    scope: AthleteScope, db: DB,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    profile, _ = scope
    owned = Workout.athlete_id == profile.athlete_id
    rows = db.execute(
        select(Workout, RpeEntry).outerjoin(RpeEntry).where(owned)
        .order_by(Workout.started_at.desc(), Workout.id.desc()).offset(offset).limit(limit + 1)
    ).all()
    missing = db.scalar(
        select(func.count()).select_from(Workout).outerjoin(RpeEntry).where(owned, RpeEntry.workout_id.is_(None))
    )
    return {
        "timezone": profile.timezone, "missing_rpe_count": missing,
        "workouts": [workout_view(workout, rpe) for workout, rpe in rows[:limit]],
        "has_more": len(rows) > limit,
    }


@router.get("/workouts/{workout_id}", response_model=WorkoutView)
def workout_detail(workout: WorkoutScope, db: DB):
    return workout_view(workout, db.get(RpeEntry, workout.id))


@router.get("/workouts/{workout_id}/rpe", response_model=RpeView | None)
def get_rpe(workout: WorkoutScope, db: DB):
    return rpe_view(workout, db.get(RpeEntry, workout.id))


@router.put("/workouts/{workout_id}/rpe", response_model=RpeView)
def save_rpe(payload: RpeValue, workout: WorkoutScope, auth: Auth, db: DB):
    if auth.user.role != "athlete":
        raise HTTPException(403, "role_denied")
    timestamp = time()
    rpe = db.get(RpeEntry, workout.id)
    if rpe is None:
        rpe = RpeEntry(workout_id=workout.id, created_at=timestamp)
        db.add(rpe)
    rpe.score = payload.score
    rpe.comment = payload.comment
    rpe.updated_at = timestamp
    db.commit()
    return rpe_view(workout, rpe)
