from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analytics import bounds, ftp_points, select_range
from ..analytics_models import AppSetting
from ..analytics_schemas import DateRange
from ..athlete_models import AthleteProfile, RpeEntry, WellnessEntry, Workout
from ..athletes import local_date
from ..workout_import import DUPLICATE_SECONDS, similar_workouts
from ..workout_models import WorkoutEffort, WorkoutMetrics
from ..workouts import rpe_view
from .load import BASELINE_DAYS, DAY, WEEK_DAYS, daily_load, week_load
from .performance import PERFORMANCE_WINDOW_DAYS, effort_view, performance_trend
from .power import POWER_PROJECTION_VERSION
from .recovery import METRICS, UNSUPPORTED, recovery_days, recovery_pattern
from .relationships import CORRELATION_DAYS, LIMITATIONS, MAX_LAG, daily_outcomes, episodes, lag_correlations
from .schemas import EffortPoint, Measurements, Performance, Reaction, Rules, Status, WorkoutLoad

SETTINGS_KEY = "training_intelligence"


def load_rules(db: Session) -> Rules:
    row = db.get(AppSetting, SETTINGS_KEY)
    return Rules.model_validate(row.value) if row is not None else Rules()


def duplicate_candidates(rows):
    """Cross-check current canonical rows with the FIT importer's similarity rule."""
    result = defaultdict(list)
    for index, (workout, *_) in enumerate(rows):
        for other_index in range(index - 1, -1, -1):
            candidate = rows[other_index][0]
            if workout.started_at - candidate.started_at > DUPLICATE_SECONDS:
                break
            newest, older = (workout, candidate) if workout.id > candidate.id else (candidate, workout)
            if similar_workouts(newest, older):
                result[workout.id].append(candidate.id)
                result[candidate.id].append(workout.id)
    return {workout_id: sorted(candidates) for workout_id, candidates in result.items()}


def sources(
    db: Session, profile: AthleteProfile, start: date, today: date, timestamp: float, rules: Rules,
) -> tuple[list[WorkoutLoad], list[EffortPoint]]:
    lower, upper = bounds(DateRange(start=start, end=today), profile.timezone)
    rows = db.execute(
        select(Workout, RpeEntry, WorkoutMetrics.result, WorkoutEffort)
        .outerjoin(RpeEntry).outerjoin(WorkoutMetrics).outerjoin(WorkoutEffort)
        .where(Workout.athlete_id == profile.athlete_id, Workout.started_at >= lower - DUPLICATE_SECONDS,
               Workout.started_at < upper, Workout.started_at <= timestamp)
        .order_by(Workout.started_at, Workout.id)
    ).all()
    duplicates = duplicate_candidates(rows)
    workouts, efforts = [], []
    for workout, rpe, calculated, effort in rows:
        if workout.started_at < lower:
            continue
        day = local_date(profile.timezone, workout.started_at)
        metrics = calculated or {}
        score = rpe_view(workout, rpe)
        timer = workout.timer_seconds
        intensity = metrics.get("if_value")
        projection = metrics.get("power_projection", {})
        reasons = dict(metrics.get("reasons", {}))
        for metric in ("tss", "np", "if_value"):
            if metrics.get(metric) is None:
                reasons.setdefault("if" if metric == "if_value" else metric, "not_recorded")
        if score is None:
            reasons["rpe"] = reasons["session_rpe"] = "rpe_missing"
        if timer is None:
            reasons["timer_seconds"] = "timer_missing"
            if score is not None:
                reasons["session_rpe"] = "timer_missing"
        candidates = duplicates.get(workout.id, [])
        workouts.append(WorkoutLoad(
            workout_id=workout.id, date=day, started_at=workout.started_at, timer_seconds=timer,
            np=metrics.get("np"), if_value=intensity, tss=metrics.get("tss"),
            rpe=score.score if score is not None else None, session_rpe=score.session_load if score is not None else None,
            intense=intensity is not None and timer is not None and intensity >= rules.intense_if
            and timer >= rules.intense_min_seconds,
            duplicate_candidates=candidates, reasons=reasons,
            best_power=projection["best_watts"] if projection.get("version") == POWER_PROJECTION_VERSION else None,
        ))
        if effort is not None:
            view = effort_view(workout, effort)
            if candidates:
                view = view.model_copy(update={"mean_watts": None, "status": "insufficient", "reason": "possible_duplicate"})
            entire_workout = effort.start_seconds == 0 and (
                effort.duration_seconds == timer == (workout.summary or {}).get("elapsed_seconds")
            )
            efforts.append(EffortPoint(
                **view.model_dump(), date=day, rpe=score.score if entire_workout and score is not None else None,
            ))
    return workouts, efforts


def measurements(
    db: Session, profile: AthleteProfile, period_days: int, timestamp: float, rules: Rules,
) -> Measurements:
    today = local_date(profile.timezone, timestamp)
    completed = today - DAY
    period = select_range(period_days, today)
    correlation_start = completed - (CORRELATION_DAYS - 1) * DAY
    analysis_start = min(period.start - MAX_LAG * DAY, correlation_start)
    history_start = analysis_start - max(
        3 * rules.ctl_days, PERFORMANCE_WINDOW_DAYS, rules.recovery_window_days, BASELINE_DAYS + WEEK_DAYS,
    ) * DAY
    workouts, efforts = sources(db, profile, history_start, today, timestamp, rules)
    diary = db.scalars(select(WellnessEntry).where(
        WellnessEntry.athlete_id == profile.athlete_id,
        WellnessEntry.entry_date.between(history_start, today),
    )).all()
    entries = {entry.entry_date: {metric: getattr(entry, metric) for metric in METRICS} for entry in diary}
    confirmations = {entry.entry_date: entry.training_day_status for entry in diary if entry.training_day_status}
    load = daily_load(history_start, today, workouts, confirmations, rules)
    by_index = {day.date: index for index, day in enumerate(load)}
    current = week_load(load, by_index[completed], rules)
    display_weeks = [
        week_load(load, index, rules)
        for index in range(by_index[completed], by_index[period.start] - 1, -WEEK_DAYS)
    ]
    recovery = recovery_days(analysis_start, today, entries, rules)
    prior_recovery = {day.date: day for day in recovery if day.date <= completed}
    recent = [day for day in prior_recovery.values() if day.date > completed - WEEK_DAYS * DAY]
    points, changes, outcomes = performance_trend(efforts, completed, rules)
    visible_attempts = [point for point in points if point.date >= period.start]
    visible_changes = [change for change in changes if change.workouts[-1].date >= period.start]
    performance_status: Status = "insufficient"
    if any(point.delta_pct is not None for point in visible_attempts):
        performance_status = "sufficient" if all(point.delta_pct is not None for point in visible_attempts) else "partial"
    performance = Performance(
        status=performance_status, efforts=visible_attempts, repeated_changes=visible_changes,
        ftp=ftp_points(db, profile.athlete_id, period),
    )
    previous_week_high = False
    elevated = []
    for index in range(by_index[analysis_start] - 1, by_index[completed] + 1):
        point = load[index]
        week = week_load(load, index, rules)
        week_high = week.delta_pct is not None and week.delta_pct >= rules.high_load_pct
        crossing = week_high and not previous_week_high
        if index >= by_index[analysis_start] and (point.elevated_streak or crossing):
            related_days = load[index + 1 - WEEK_DAYS:index + 1] if crossing else [point]
            key_workouts = [workout_id for day in related_days for workout_id in day.workout_ids]
            elevated.append(point.model_copy(update={"workout_ids": key_workouts}))
        previous_week_high = week_high
    observed_reactions = [
        Reaction(date=day.date, metric=metric, value=value.value, baseline=value.baseline, delta=value.delta)
        for day in prior_recovery.values() for metric, value in day.metrics.items() if value.deteriorated
    ]
    observed_reactions.extend(
        outcome for outcome in outcomes
        if abs(100 * outcome.delta / outcome.baseline) >= rules.performance_change_pct
    )
    correlations = lag_correlations(
        {day.date: day.delta_tss for day in load if day.delta_tss is not None},
        {day.date for day in load if day.coverage.confirmed_days},
        daily_outcomes(recovery, outcomes), completed, rules,
    )
    return Measurements(
        athlete_id=profile.athlete_id, timezone=profile.timezone, period=period, completed_through=completed, rules=rules,
        workouts=[workout for workout in workouts if workout.date >= period.start],
        load=load[by_index[period.start]:], weeks=list(reversed(display_weeks)), current_week=current,
        recovery=[day for day in recovery if day.date >= period.start], recovery_pattern=recovery_pattern(recent),
        missing_diary_dates=[day.date for day in prior_recovery.values() if day.date >= period.start and not day.has_entry],
        unsupported_recovery=UNSUPPORTED, performance=performance,
        episodes=[item for item in episodes(elevated, observed_reactions, prior_recovery, completed)
                  if item.reaction_window_end >= period.start],
        correlations=correlations, relationship_limitations=LIMITATIONS,
    )
