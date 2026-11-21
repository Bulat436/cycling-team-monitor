from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from itertools import groupby
from math import fsum, sqrt
from statistics import median
from typing import Literal

from ..analytics_schemas import DateRange
from .load import DAY
from .schemas import Correlation, LinkedReaction, LoadDay, LoadEpisode, LoadExposure, Reaction, RecoveryDay, Rules

MAX_LAG = 7
CORRELATION_DAYS = 180
MIN_HALF_PAIRS = 12
LAG_GROUPS: tuple[Literal["0", "1", "2", "3", "4-7"], ...] = ("0", "1", "2", "3") + ("4-7",) * 4
LIMITATIONS = [
    "same_date_order_unknown",
    "observational_association_only",
    "neighboring_dates_and_lags_may_depend_on_each_other",
    "available_pairs_only",
]


def normalization_dates(
    last_load: date, reactions: Sequence[LinkedReaction], recovery: Mapping[date, RecoveryDay],
    required_days: int,
) -> list[date]:
    degraded = [reaction for reaction in reactions if reaction.workout_id is None]
    if not degraded:
        return []
    metrics = {reaction.metric for reaction in degraded}
    after = max(last_load, degraded[-1].date)
    normal = []
    for offset in range(1, MAX_LAG + 1):
        day = last_load + offset * DAY
        if day <= after or day not in recovery:
            continue
        observations = [recovery[day].metrics[metric] for metric in sorted(metrics)]
        if any(value.deteriorated for value in observations):
            normal.clear()
        elif all(value.within_baseline for value in observations):
            normal.append(day)
            if len(normal) == required_days:
                return normal
    return []


def episodes(
    elevated: Sequence[LoadDay], reactions: Sequence[Reaction], recovery: Mapping[date, RecoveryDay],
    completed_through: date, normalization_min_days: int, exposures: Mapping[date, LoadExposure] | None = None,
) -> list[LoadEpisode]:
    blocks: list[list[LoadDay]] = []
    for day in elevated:
        if blocks and (day.date - blocks[-1][-1].date).days <= MAX_LAG:
            blocks[-1].append(day)
        else:
            blocks.append([day])

    ordered = sorted(
        {(item.date, item.metric, item.workout_id): item for item in reactions}.values(),
        key=lambda item: (item.date, item.metric, item.workout_id or 0),
    )
    result = []
    for block in blocks:
        first, last = block[0].date, block[-1].date
        end = last + MAX_LAG * DAY
        linked = []
        exposure_index = 0
        for item in ordered:
            if not first <= item.date <= min(end, completed_through):
                continue
            while exposure_index + 1 < len(block) and block[exposure_index + 1].date <= item.date:
                exposure_index += 1
            load_date = block[exposure_index].date
            lag = (item.date - load_date).days
            linked.append(LinkedReaction(
                **item.model_dump(), load_date=load_date, lag_days=lag, lag_group=LAG_GROUPS[lag],
                same_date_only=lag == 0,
            ))
        result.append(LoadEpisode(
            period=DateRange(start=first, end=last), elevated_dates=[item.date for item in block],
            workout_ids=list(dict.fromkeys(workout_id for item in block for workout_id in item.workout_ids)),
            reaction_window_end=end, open=end > completed_through, reactions=linked,
            exposures=[exposures[item.date] for item in block] if exposures is not None else [],
            normalization_dates=normalization_dates(last, linked, recovery, normalization_min_days),
        ))
    return result


def ranks(values: Sequence[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    result = [0.0] * len(ordered)
    position = 1
    for _, group in groupby(ordered, key=lambda item: item[1]):
        tied = list(group)
        average_rank = position + (len(tied) - 1) / 2
        for index, _ in tied:
            result[index] = average_rank
        position += len(tied)
    return result


def spearman(pairs: Sequence[tuple[float, float]]) -> float | None:
    x, y = zip(*pairs, strict=True)
    center = (len(pairs) + 1) / 2
    xr, yr = [value - center for value in ranks(x)], [value - center for value in ranks(y)]
    scale = sqrt(fsum(value * value for value in xr) * fsum(value * value for value in yr))
    if scale == 0:
        return None
    return max(-1.0, min(1.0, fsum(a * b for a, b in zip(xr, yr, strict=True)) / scale))


def lag_correlations(
    load: Mapping[date, float], confirmed: set[date], outcomes: Mapping[str, Mapping[date, float]],
    completed_through: date, rules: Rules,
) -> list[Correlation]:
    start = completed_through - (CORRELATION_DAYS - 1) * DAY
    sources = sorted(day for day in load if start <= day <= completed_through)
    result = []
    for metric, values in sorted(outcomes.items()):
        for lag in range(MAX_LAG + 1):
            aligned = [
                (day, response, load[day], values[response])
                for day in sources
                if (response := day + lag * DAY) <= completed_through and response in values
            ]
            count = len(aligned)
            confirmed_count = sum(day in confirmed for day, _, _, _ in aligned)
            period = DateRange(start=aligned[0][0], end=aligned[-1][1]) if aligned else None
            coefficient = stable = halves = None
            if count >= rules.correlation_min_pairs:
                pairs = [(x, y) for _, _, x, y in aligned]
                coefficient = spearman(pairs)
                midpoint = count // 2
                if coefficient is not None and min(midpoint, count - midpoint) >= MIN_HALF_PAIRS:
                    left, right = spearman(pairs[:midpoint]), spearman(pairs[midpoint:])
                    halves = (left, right)
                    sign = 1 if coefficient > 0 else -1
                    stable = (
                        abs(coefficient) >= rules.correlation_min_half_abs
                        and left is not None and right is not None
                        and min(left * sign, right * sign) >= rules.correlation_min_half_abs
                    )
            result.append(Correlation(
                metric=metric, lag_days=lag, pairs=count, confirmed_load_pairs=confirmed_count,
                period=period, coefficient=coefficient,
                stable=stable, halves=halves, status=(
                    "missing" if count == 0 else "insufficient" if coefficient is None
                    else "sufficient" if stable and confirmed_count == count else "partial"
                ),
            ))
    return result


def daily_outcomes(
    recovery: Sequence[RecoveryDay], performance: Sequence[Reaction],
) -> dict[str, dict[date, float]]:
    daily = defaultdict(dict)
    for day in recovery:
        for metric, value in day.metrics.items():
            series = daily[metric]
            if value.delta is not None:
                series[day.date] = value.delta
    same_day = defaultdict(list)
    for attempt in performance:
        same_day[attempt.metric, attempt.date].append(attempt.delta)
    for (metric, day), deltas in same_day.items():
        daily[metric][day] = median(deltas)
    return dict(daily)
