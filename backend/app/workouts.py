import json
import zlib
from time import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .athlete_models import AthleteProfile, RpeEntry, Workout
from .athlete_schemas import RpeValue, RpeView, WorkoutList, WorkoutView
from .athletes import AthleteScope
from .fit_parser import MAX_SAMPLES, FitProblem
from .security import DB, Auth
from .workout_import import BLOCK_SIZE
from .workout_models import WorkoutFile, WorkoutMetrics, WorkoutSampleBlock, WorkoutSource
from .workout_schemas import MetricsView, SamplePage, SourceView, WorkoutDetail

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


def workout_view(workout: Workout, rpe: RpeEntry | None, metrics: dict | None) -> WorkoutView:
    calculated = metrics or {}
    return WorkoutView(
        id=workout.id, athlete_id=workout.athlete_id, started_at=workout.started_at,
        timer_seconds=workout.timer_seconds, rpe=rpe_view(workout, rpe), summary=workout.summary,
        np=calculated.get("np"), tss=calculated.get("tss"),
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
        select(Workout, RpeEntry, WorkoutMetrics.result).outerjoin(RpeEntry).outerjoin(WorkoutMetrics).where(owned)
        .order_by(Workout.started_at.desc(), Workout.id.desc()).offset(offset).limit(limit + 1)
    ).all()
    missing = db.scalar(
        select(func.count()).select_from(Workout).outerjoin(RpeEntry).where(owned, RpeEntry.workout_id.is_(None))
    )
    return {
        "timezone": profile.timezone, "missing_rpe_count": missing,
        "workouts": [workout_view(workout, rpe, metrics) for workout, rpe, metrics in rows[:limit]],
        "has_more": len(rows) > limit,
    }


def metrics_view(metrics: WorkoutMetrics | None) -> MetricsView | None:
    if metrics is None:
        return None
    return MetricsView(version=metrics.version, snapshot=metrics.snapshot, result=metrics.result)


@router.get("/workouts/{workout_id}", response_model=WorkoutDetail)
def workout_detail(workout: WorkoutScope, db: DB):
    metrics = db.get(WorkoutMetrics, workout.id)
    stats = workout_view(workout, db.get(RpeEntry, workout.id), metrics.result if metrics else None)
    profile = db.get(AthleteProfile, workout.athlete_id)
    sources = db.scalars(select(WorkoutSource).where(WorkoutSource.workout_id == workout.id)
                         .order_by(WorkoutSource.source, WorkoutSource.source_key)).all()
    return WorkoutDetail(
        **stats.model_dump(), timezone=metrics.snapshot["timezone"] if metrics else profile.timezone,
        metrics=metrics_view(metrics), sources=[SourceView(source=row.source, external_id=row.external_id) for row in sources],
        original_available=workout.file_sha256 is not None, metadata=metrics.metadata_json if metrics else None,
    )


@router.get("/workouts/{workout_id}/metrics", response_model=MetricsView | None)
def workout_metrics(workout: WorkoutScope, db: DB):
    return metrics_view(db.get(WorkoutMetrics, workout.id))


def read_sample_blocks(db: Session, workout_id: int, block_range: tuple[int, int] | None = None):
    query = select(WorkoutSampleBlock.payload).where(WorkoutSampleBlock.workout_id == workout_id)
    if block_range is not None:
        query = query.where(WorkoutSampleBlock.block_index.between(*block_range))
    for payload in db.scalars(query.order_by(WorkoutSampleBlock.block_index).execution_options(yield_per=8)):
        yield json.loads(zlib.decompress(payload))


@router.get("/workouts/{workout_id}/samples", response_model=SamplePage)
def workout_samples(
    workout: WorkoutScope, db: DB,
    offset: Annotated[int, Query(ge=0, le=MAX_SAMPLES)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 512,
    include_fields: bool = False,
):
    total = (workout.summary or {}).get("sample_count", 0)
    stop = min(offset + limit, total)
    rows = []
    if offset < stop:
        first_block, last_block = offset // BLOCK_SIZE, (stop - 1) // BLOCK_SIZE
        for block in read_sample_blocks(db, workout.id, (first_block, last_block)):
            rows.extend(block)
        start = offset % BLOCK_SIZE
        rows = rows[start:start + stop - offset]
    if not include_fields:
        for row in rows:
            row.pop("fields", None)
    return {"samples": rows, "offset": offset, "total": total}


@router.get("/workouts/{workout_id}/fit")
def original_fit(workout: WorkoutScope, db: DB, request: Request):
    if workout.file_sha256 is None:
        raise HTTPException(404, "fit_original_missing")
    record = db.get(WorkoutFile, (workout.athlete_id, workout.file_sha256))
    if record is None or record.workout_id != workout.id:
        raise HTTPException(404, "fit_original_missing")
    try:
        path = request.app.state.workout_import.original_path(workout.athlete_id, record.sha256)
        if not path.is_file():
            raise HTTPException(404, "fit_original_missing")
    except FitProblem as exc:
        raise HTTPException(exc.status, exc.code) from exc
    return FileResponse(path, media_type="application/octet-stream", filename=record.filename)


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
