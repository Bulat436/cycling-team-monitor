from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from statistics import median

from ..athlete_models import Workout
from ..workout_models import WorkoutEffort
from .load import DAY, percent
from .power import POWER_PROJECTION_VERSION
from .schemas import EffortPoint, EffortView, PerformanceChange, Reaction, Rules

def effort_view(workout: Workout, effort: WorkoutEffort) -> EffortView:
    current = effort.file_sha256 == workout.file_sha256 and effort.projection_version == POWER_PROJECTION_VERSION
    return EffortView(
        workout_id=workout.id, protocol=effort.protocol, start_seconds=effort.start_seconds,
        duration_seconds=effort.duration_seconds, mean_watts=effort.mean_watts if current else None,
        status="sufficient" if current else "insufficient",
        reason=None if current else "fit_or_algorithm_changed",
    )


def comparable_history(attempts: Sequence[EffortPoint], before: date, window_days: int) -> list[EffortPoint]:
    unique_days = {
        attempt.date: attempt for attempt in attempts
        if before - window_days * DAY <= attempt.date < before
    }
    return list(unique_days.values())


def compare(attempt: EffortPoint, history: Sequence[EffortPoint], rules: Rules) -> EffortPoint:
    if attempt.mean_watts is None:
        return attempt
    if len(history) < rules.performance_baseline_attempts:
        return attempt.model_copy(update={"status": "insufficient", "reason": "too_few_comparable_attempts"})
    baseline = median(point.mean_watts for point in history)
    delta = percent(attempt.mean_watts, baseline)
    rpe = [
        point.rpe for point in history
        if attempt.rpe is not None and point.rpe is not None and point.mean_watts > 0
        and abs(percent(attempt.mean_watts, point.mean_watts)) < rules.performance_change_pct
    ]
    return attempt.model_copy(update={
        "status": "sufficient" if delta is not None else "partial",
        "reason": None if delta is not None else "zero_power_baseline",
        "baseline_watts": baseline, "baseline_workout_ids": [point.workout_id for point in history],
        "delta_pct": delta, "rpe_observations": len(rpe),
        "baseline_rpe": median(rpe) if len(rpe) >= rules.performance_baseline_attempts else None,
    })


def performance_trend(
    efforts: Sequence[EffortPoint], completed_through: date, rules: Rules,
) -> tuple[list[EffortPoint], list[PerformanceChange], list[Reaction]]:
    """Analyze measured attempts from a single athlete with protocol-specific history."""
    groups = defaultdict(list)
    points = []
    changes = []
    outcomes = []
    for effort in sorted(efforts, key=lambda point: point.date):
        groups[effort.protocol, effort.duration_seconds].append(effort)
    for (protocol, duration), group in sorted(groups.items()):
        completed = [attempt for attempt in group if attempt.date <= completed_through]
        measured = [attempt for attempt in completed if attempt.mean_watts is not None]
        for attempt in group:
            point = compare(attempt, comparable_history(measured, attempt.date, rules.performance_window_days), rules)
            points.append(point)
            if point.delta_pct is not None and attempt.date <= completed_through:
                outcomes.append(Reaction(
                    date=point.date, metric=f"performance:{duration}:{protocol}", workout_id=point.workout_id,
                    value=point.mean_watts, baseline=point.baseline_watts,
                    delta=point.mean_watts - point.baseline_watts, observations=len(point.baseline_workout_ids),
                ))
        latest = list({attempt.date: attempt for attempt in completed}.values())[-rules.performance_repeat_attempts:]
        if (len(latest) != rules.performance_repeat_attempts or any(attempt.mean_watts is None for attempt in latest)
                or (latest[-1].date - latest[0].date).days > rules.performance_window_days):
            continue
        history = comparable_history(measured, latest[0].date, rules.performance_window_days)
        compared = [compare(attempt, history, rules) for attempt in latest]
        changes_pct = [attempt.delta_pct for attempt in compared if attempt.delta_pct is not None]
        if len(changes_pct) != len(latest):
            continue
        threshold = rules.performance_change_pct
        decreased = max(changes_pct) <= -threshold
        improved = min(changes_pct) >= threshold
        if decreased or improved:
            changes.append(PerformanceChange(
                protocol=protocol, duration_seconds=duration, direction="decreased" if decreased else "improved",
                threshold_pct=threshold, baseline_watts=compared[0].baseline_watts,
                baseline_workout_ids=compared[0].baseline_workout_ids, baseline_attempts=history, workouts=compared,
            ))
    return sorted(points, key=lambda point: (point.date, point.workout_id)), changes, outcomes
