from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from math import exp, expm1, fsum
from statistics import median

from ..analytics_schemas import DateRange
from .schemas import Coverage, DayStatus, LoadDay, LoadReferenceBlock, LoadWeek, Rules, Status, WorkoutLoad

DAY = timedelta(days=1)
WEEK_DAYS = 7
BASELINE_DAYS = 28
MIN_BASELINE_WEEKS = 3


def dates(start: date, end: date):
    return (start + DAY * offset for offset in range((end - start).days + 1))


def known_sum(values: Iterable[float | None]) -> float | None:
    observed = [value for value in values if value is not None]
    return fsum(observed) if observed else None


def percent(value: float, baseline: float) -> float | None:
    return 100 * (value - baseline) / baseline if baseline > 0 else None


def coverage(days: Sequence[LoadDay]) -> Coverage:
    return Coverage(
        days=len(days),
        known_days=sum(day.coverage.known_days for day in days),
        confirmed_days=sum(day.coverage.confirmed_days for day in days),
        workouts=sum(day.coverage.workouts for day in days),
        workouts_with_tss=sum(day.coverage.workouts_with_tss for day in days),
    )


def day_inputs(day: date, entries: Sequence[WorkoutLoad], confirmation: str | None, today: date) -> LoadDay:
    if day >= today:
        confirmation = None
    conflict = (
        any(entry.duplicate_candidates for entry in entries)
        or (confirmation == "rest" and bool(entries))
        or (confirmation == "all_workouts_recorded" and not entries)
    )
    status: DayStatus
    if conflict:
        status = "conflict"
    elif confirmation == "rest":
        status = "rest"
    elif confirmation == "all_workouts_recorded":
        status = "all_workouts_recorded"
    else:
        status = "imported" if entries else "unknown"
    tss_count = sum(entry.tss is not None for entry in entries)
    recorded = 0.0 if status == "rest" else known_sum(entry.tss for entry in entries)
    tss = recorded if not conflict and tss_count == len(entries) else None
    return LoadDay(
        date=day, day_status=status, confirmation=confirmation, preliminary=day >= today,
        coverage=Coverage(
            days=1, known_days=int(tss is not None),
            confirmed_days=int(status in {"rest", "all_workouts_recorded"}),
            workouts=len(entries), workouts_with_tss=tss_count,
        ),
        recorded_tss=recorded, tss=tss, workout_ids=[entry.workout_id for entry in entries],
        timer_seconds=known_sum(entry.timer_seconds for entry in entries),
        session_rpe=known_sum(entry.session_rpe for entry in entries),
        intense_workouts=sum(entry.intense for entry in entries),
    )


@dataclass
class Smoothing:
    numerator: float = 0
    weight: float = 0

    def update(self, value: float | None, decay: float, alpha: float) -> float | None:
        if value is None:
            self.numerator *= decay
            self.weight *= decay
            return None
        if self.weight == 0:
            self.numerator, self.weight = value, 1
        else:
            self.numerator = decay * self.numerator + alpha * value
            self.weight = decay * self.weight + alpha
        return self.numerator / self.weight


def curve_status(day: LoadDay, history: Coverage, period: int, value: float | None) -> Status:
    if value is None:
        return "missing" if day.day_status == "unknown" else "insufficient"
    if history.confirmed_days == history.known_days == period and not day.preliminary:
        return "sufficient"
    return "partial" if history.known_days > 1 else "insufficient"


def daily_load(
    start: date, today: date, workouts: Iterable[WorkoutLoad], confirmations: dict[date, str], rules: Rules,
) -> list[LoadDay]:
    groups = defaultdict(list)
    for workout in workouts:
        groups[workout.date].append(workout)
    days = []
    atl, ctl = Smoothing(), Smoothing()
    atl_decay, ctl_decay = exp(-1 / rules.atl_days), exp(-1 / rules.ctl_days)
    atl_alpha, ctl_alpha = -expm1(-1 / rules.atl_days), -expm1(-1 / rules.ctl_days)
    streak = 0
    for index, day in enumerate(dates(start, today)):
        point = day_inputs(day, groups[day], confirmations.get(day), today)
        baseline_values = [prior.tss for prior in days[-BASELINE_DAYS:] if prior.tss is not None]
        point.baseline_observations = len(baseline_values)
        if len(baseline_values) >= rules.load_baseline_min_days:
            point.baseline = median(baseline_values)
        if point.tss is not None and point.baseline is not None:
            point.delta_tss = point.tss - point.baseline
            point.delta_pct = percent(point.tss, point.baseline)
        elevated = point.delta_pct is not None and point.delta_pct >= rules.high_load_pct
        streak = streak + 1 if elevated and not point.preliminary else 0
        point.elevated_streak = streak
        days.append(point)
        point.atl = atl.update(point.tss, atl_decay, atl_alpha)
        point.ctl = ctl.update(point.tss, ctl_decay, ctl_alpha)
        point.atl_coverage = coverage(days[max(0, index + 1 - rules.atl_days):])
        point.ctl_coverage = coverage(days[max(0, index + 1 - rules.ctl_days):])
        point.atl_status = curve_status(point, point.atl_coverage, rules.atl_days, point.atl)
        point.ctl_status = curve_status(point, point.ctl_coverage, rules.ctl_days, point.ctl)
        point.tsb_status = min(
            (point.atl_status, point.ctl_status),
            key=("missing", "insufficient", "partial", "sufficient").index,
        )
        if point.atl is not None and point.ctl is not None:
            point.tsb = point.ctl - point.atl
    return days


def week_load(days: Sequence[LoadDay], end: int, rules: Rules) -> LoadWeek:
    """Summarize seven completed days ending at index end (inclusive)."""
    current = days[end + 1 - WEEK_DAYS:end + 1]
    observed = coverage(current)
    usable = observed.known_days >= rules.weekly_min_days and not any(day.day_status == "conflict" for day in current)
    blocks = []
    sums = []
    fully_confirmed = observed.confirmed_days == observed.known_days == WEEK_DAYS
    previous = None
    if usable:
        for offset in range(WEEK_DAYS, BASELINE_DAYS + 1, WEEK_DAYS):
            stop = end + 1 - offset
            block = days[max(0, stop - WEEK_DAYS):stop] if stop > 0 else []
            sampled = coverage(block)
            if (len(block) != WEEK_DAYS or sampled.known_days != observed.known_days
                    or any(day.day_status == "conflict" for day in block)):
                continue
            measured = fsum(day.tss for day in block if day.tss is not None)
            if offset == WEEK_DAYS:
                previous = measured
            blocks.append(LoadReferenceBlock(
                period=DateRange(start=block[0].date, end=block[-1].date), coverage=sampled, tss=measured,
            ))
            sums.append(measured)
            fully_confirmed &= sampled.confirmed_days == WEEK_DAYS
    comparable = fsum(day.tss for day in current if day.tss is not None) if usable else None
    baseline = median(sums) if len(sums) >= MIN_BASELINE_WEEKS else None
    recorded = known_sum(day.recorded_tss for day in current)
    status: Status = "missing" if recorded is None and not observed.workouts else "insufficient"
    if baseline is not None:
        status = "sufficient" if fully_confirmed else "partial"
    return LoadWeek(
        period=DateRange(start=current[0].date, end=current[-1].date), coverage=observed,
        recorded_tss=recorded, timer_seconds=known_sum(day.timer_seconds for day in current),
        session_rpe=known_sum(day.session_rpe for day in current),
        intense_workouts=sum(day.intense_workouts for day in current),
        rest_days=sum(day.day_status == "rest" for day in current), baseline_blocks=blocks,
        baseline_tss=baseline, comparable_tss=comparable,
        delta_tss=comparable - baseline if comparable is not None and baseline is not None else None,
        delta_pct=percent(comparable, baseline) if comparable is not None and baseline is not None else None,
        previous_week_tss=previous,
        change_from_previous_pct=percent(comparable, previous) if comparable is not None and previous is not None else None,
        status=status,
    )
