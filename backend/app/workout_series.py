from collections import defaultdict
from math import ceil
from typing import Annotated

from fastapi import APIRouter, Query

from .analytics_schemas import SamplePoint, WorkoutSeries
from .fit_parser import MAX_ACTIVITY_SECONDS
from .security import DB, Coach
from .workout_metrics import MAX_HOLD
from .workouts import WorkoutScope, read_sample_blocks

router = APIRouter(tags=["Аналитика"])
SENSORS = ("power", "heart_rate", "cadence")


def sensor_series(samples, started_at: int, buckets: int):
    measured = {}
    for sample in samples:
        timestamp = sample["timestamp"]
        if timestamp is None or not started_at <= timestamp <= started_at + MAX_ACTIVITY_SECONDS:
            continue
        values = measured.setdefault(timestamp - started_at, {})
        values.update({channel: sample[channel] for channel in SENSORS if sample[channel] is not None})
    ordered = sorted(measured.items())
    if not ordered:
        return {channel: [] for channel in SENSORS}

    width = max(1, ceil((ordered[-1][0] + 1) / buckets))
    result = {}
    for channel in SENSORS:
        grouped = defaultdict(list)
        segment = 0
        previous = None
        for second, values in ordered:
            value = values.get(channel)
            if value is None:
                if previous is not None:
                    segment += 1
                previous = None
                continue
            if previous is not None and second - previous > MAX_HOLD:
                segment += 1
            grouped[second // width].append((second, value, segment))
            previous = second

        points = []
        last = None
        for group in grouped.values():
            candidates = {group[0], min(group, key=lambda row: row[1]), max(group, key=lambda row: row[1]), group[-1]}
            for point in sorted(candidates):
                second, value, segment = point
                if last and segment != last[2]:
                    points.append(SamplePoint(second=last[0] + 1, value=None))
                points.append(SamplePoint(second=second, value=value))
                last = point
        result[channel] = points
    return result


@router.get("/workouts/{workout_id}/series", response_model=WorkoutSeries)
def workout_series(
    workout: WorkoutScope, db: DB, coach: Coach,
    buckets: Annotated[int, Query(ge=100, le=1000)] = 500,
):
    return sensor_series(
        (row for block in read_sample_blocks(db, workout.id) for row in block),
        workout.started_at, buckets,
    )
