from collections.abc import Mapping, Sequence
from datetime import date
from statistics import median

from .load import DAY, dates
from .schemas import RecoveryDay, RecoveryPattern, RecoveryValue, Rules

# Metric -> (independent group, direction in which the value worsens).
METRICS = {
    "sleep_quality": ("sleep", -1),
    "sleep_minutes": ("sleep", -1),
    "general_fatigue": ("fatigue", 1),
    "leg_fatigue": ("fatigue", 1),
    "recovery": ("recovery", -1),
    "motivation": ("motivation", -1),
    "mood": ("mood", -1),
    "muscle_soreness": ("soreness", 1),
}
UNSUPPORTED = ["stress", "resting_heart_rate", "hrv"]


def recovery_days(
    start: date, end: date, entries: Mapping[date, dict[str, int | None]], rules: Rules,
) -> list[RecoveryDay]:
    result = []
    for day in dates(start, end):
        previous = [
            entries[earlier]
            for offset in range(1, rules.recovery_window_days + 1)
            if (earlier := day - DAY * offset) in entries
        ]
        values = {}
        for metric, (_, direction) in METRICS.items():
            observed = [value for row in previous if (value := row.get(metric)) is not None]
            baseline = median(observed) if len(observed) >= rules.recovery_min_observations else None
            value = entries.get(day, {}).get(metric)
            delta = value - baseline if value is not None and baseline is not None else None
            threshold = (
                rules.recovery_sleep_delta_minutes if metric == "sleep_minutes" else rules.recovery_score_delta
            )
            values[metric] = RecoveryValue(
                value=value, baseline=baseline, observations=len(observed), delta=delta,
                status="missing" if value is None else "insufficient" if baseline is None else "sufficient",
                deteriorated=delta * direction >= threshold if delta is not None else None,
            )
        result.append(RecoveryDay(date=day, has_entry=day in entries, metrics=values))
    return result


def recovery_pattern(days: Sequence[RecoveryDay]) -> RecoveryPattern:
    evaluated = []
    deterioration = []
    for day in days:
        known_groups = {METRICS[key][0] for key, value in day.metrics.items() if value.delta is not None}
        if len(known_groups) >= 2:
            evaluated.append(day.date)
        groups = {METRICS[key][0] for key, value in day.metrics.items() if value.deteriorated}
        if len(groups) >= 2:
            deterioration.append(day.date)
    return RecoveryPattern(
        evaluated_dates=evaluated, deterioration_dates=deterioration,
        sustained=len(deterioration) >= 2 if len(evaluated) >= 4 else None,
    )
