from collections import defaultdict
from collections.abc import Sequence
from datetime import date

from ..analytics_schemas import DateRange
from .load import DAY, WEEK_DAYS, coverage
from .recovery import recovery_pattern
from .schemas import LinkedReaction, LoadEpisode, LoadWeek, Measurements, PerformanceChange, RecoveryDay, Status
from .warning_schemas import (
    AnalyticsWarning, AthleteWarnings, Attention, WarningCoverage, WarningFact, WarningWorkout,
)

ATTENTION_ORDER = {None: 0, "informational": 1, "moderate": 2, "high": 3}
OBSERVATIONAL = "Это наблюдение по записанным данным. Оно не устанавливает причинную связь."
SAME_DAY = "Для событий в один день порядок внутри дня неизвестен."
PARTIAL_LOAD = "TSS относится к доступным записям. Часть дней или тренировок не подтверждена."
PROTOCOL_LIMIT = "Сравнение относится к размеченному протоколу. Сопоставимость условий зависит от разметки тренера."
RECOVERY_GAPS = "Дни без достаточных записей не подтверждают стабильное восстановление."


def in_period(day: date, period: DateRange) -> bool:
    return period.start <= day <= period.end


def episode_id(data: Measurements, episode: LoadEpisode) -> str:
    return f"{data.athlete_id}:load:{episode.period.start.isoformat()}"


def load_refs(data: Measurements, period: DateRange | None, workout_ids: Sequence[int] = ()) -> list[WarningWorkout]:
    selected = set(workout_ids)
    return [
        WarningWorkout(workout_id=workout.workout_id, date=workout.date, role="load")
        for workout in data.workouts
        if workout.workout_id in selected or (period is not None and in_period(workout.date, period))
    ]


def week_fact(week: LoadWeek, *, previous: bool = False) -> WarningFact:
    previous_period = DateRange(start=week.period.start - WEEK_DAYS * DAY, end=week.period.start - DAY)
    baseline = week.previous_week_tss if previous else week.baseline_tss
    return WarningFact(
        metric="weekly_tss", unit="tss", value=week.comparable_tss, baseline=baseline,
        baseline_kind="previous_week" if previous else "individual",
        delta=week.comparable_tss - baseline if previous else week.delta_tss,
        delta_pct=week.change_from_previous_pct if previous else week.delta_pct,
        period=week.period, reference_period=previous_period if previous else None,
        observations=1 if previous else len(week.baseline_blocks),
    )


def diary_fact(day: RecoveryDay, metric: str, reaction: LinkedReaction | None = None) -> WarningFact:
    point = day.metrics[metric]
    return WarningFact(
        metric=metric, unit="minutes" if metric == "sleep_minutes" else "points",
        value=point.value, baseline=point.baseline, baseline_kind="individual",
        delta=point.delta, date=day.date, observations=point.observations,
        load_date=reaction.load_date if reaction else None, lag_days=reaction.lag_days if reaction else None,
    )


def load_coverage(data: Measurements, *, gaps: list[str] | None = None) -> WarningCoverage:
    week, pattern = data.current_week, data.recovery_pattern
    return WarningCoverage(
        status=week.status, load=week.coverage, load_status=week.status, load_reference_blocks=week.baseline_blocks,
        recovery_period=week.period, evaluated_recovery_dates=pattern.evaluated_dates,
        worsened_recovery_dates=pattern.deterioration_dates, gaps=gaps or [],
    )


def diary_evidence(data: Measurements):
    week, pattern, rules = data.current_week, data.recovery_pattern, data.rules
    worsening = set(pattern.deterioration_dates)
    eligible = []
    resolved = set()
    for episode in data.episodes:
        linked = [
            reaction for reaction in episode.reactions
            if reaction.workout_id is None and reaction.date in worsening and in_period(reaction.load_date, week.period)
        ]
        linked_days = {reaction.date for reaction in linked}
        if not linked_days:
            continue
        if episode.normalization_dates and episode.normalization_dates[-1] > max(linked_days):
            resolved.update(linked_days)
        elif len(linked_days) >= rules.recovery_min_worsened_days:
            eligible.append((episode, linked))
    return eligible, worsening - resolved


def fatigue_and_growth(data: Measurements, diary: dict[date, RecoveryDay]) -> list[AnalyticsWarning]:
    week, pattern, rules = data.current_week, data.recovery_pattern, data.rules
    if pattern.sustained is not True:
        return []
    episodes, worsening = diary_evidence(data)
    limits = [OBSERVATIONAL, SAME_DAY, RECOVERY_GAPS]
    result = []
    elevated = week.delta_pct is not None and week.delta_pct >= rules.high_load_pct
    if elevated:
        for block, linked in episodes:
            key = episode_id(data, block)
            linked_days = sorted({reaction.date for reaction in linked})
            attention: Attention = (
                "high" if week.status == "sufficient" and len(linked_days) >= rules.warning_high_worsened_days
                else "moderate"
            )
            supporting = load_coverage(data).model_copy(update={"worsened_recovery_dates": linked_days})
            result.append(AnalyticsWarning(
                id=key + ":A", episode_id=key, type="possible_accumulated_fatigue",
                title="Возможное накопление усталости", attention=attention, period=week.period,
                workouts=load_refs(data, week.period, block.workout_ids),
                facts=[week_fact(week), *(diary_fact(diary[item.date], item.metric, item) for item in linked)],
                coverage=supporting,
                explanation=f"Недельный TSS выше индивидуальной нормы. В этом эпизоде ухудшилось несколько "
                f"показателей восстановления в {len(linked_days)} из {len(pattern.evaluated_dates)} оценённых дней.",
                limitations=limits if week.status == "sufficient" else [*limits, PARTIAL_LOAD],
                thresholds={"above_baseline_pct": rules.high_load_pct, "high_worsened_days": rules.warning_high_worsened_days},
            ))
    grew = week.change_from_previous_pct is not None and week.change_from_previous_pct >= rules.warning_growth_pct
    if not grew or len(worsening) < rules.recovery_min_worsened_days:
        return result
    recent_start = week.period.end - (rules.warning_growth_recent_days - 1) * DAY
    if max(worsening) < recent_start:
        return result
    previous_period = DateRange(start=week.period.start - WEEK_DAYS * DAY, end=week.period.start - DAY)
    previous = recovery_pattern([day for day in diary.values() if in_period(day.date, previous_period)], rules)
    if previous.sustained is None or len(worsening) <= len(previous.deterioration_dates):
        return result
    previous_block = next(block for block in week.baseline_blocks if block.period == previous_period)
    confirmed = week.coverage.confirmed_days == previous_block.coverage.confirmed_days == WEEK_DAYS
    comparison_status = "sufficient" if confirmed else "partial"
    block, linked = episodes[0] if episodes else (None, [])
    key = episode_id(data, block) if block is not None else None
    linked_lookup = {(item.date, item.metric): item for item in linked}
    facts = [
        diary_fact(diary[day], metric, linked_lookup.get((day, metric)))
        for day in sorted(worsening) for metric, point in diary[day].metrics.items() if point.deteriorated
    ]
    result.append(AnalyticsWarning(
        id=(key or f"{data.athlete_id}:week:{week.period.start}") + ":B", episode_id=key,
        type="load_growth_with_worsening_recovery", title="Рост нагрузки на фоне ухудшения восстановления",
        attention="moderate", period=week.period, workouts=load_refs(data, week.period),
        facts=[
            week_fact(week, previous=True),
            *([week_fact(week)] if week.baseline_tss is not None else []),
            WarningFact(
                metric="days_with_multiple_worsening_metrics", unit="days", value=len(worsening),
                baseline=len(previous.deterioration_dates), baseline_kind="previous_week",
                delta=len(worsening) - len(previous.deterioration_dates),
                period=week.period, reference_period=previous_period, observations=len(pattern.evaluated_dates),
            ),
            *facts,
        ],
        coverage=load_coverage(data).model_copy(update={
            "status": comparison_status, "load_status": comparison_status, "worsened_recovery_dates": sorted(worsening),
        }),
        explanation="Недельный TSS вырос относительно предыдущей сопоставимой недели. К концу периода "
        "стало больше дней с ухудшением нескольких показателей восстановления.",
        limitations=limits if confirmed else [*limits, PARTIAL_LOAD],
        thresholds={"week_growth_pct": rules.warning_growth_pct, "late_recovery_days": rules.warning_growth_recent_days},
    ))
    return result


def performance_warnings(data: Measurements, diary: dict[date, RecoveryDay], recent_start: date) -> list[AnalyticsWarning]:
    week, rules = data.current_week, data.rules
    result = []
    for change in data.performance.repeated_changes:
        if change.workouts[-1].date < recent_start:
            continue
        improved = change.direction == "improved"
        if improved and (
            week.status != "sufficient" or week.delta_pct is None or week.delta_pct < rules.high_load_pct
            or data.recovery_pattern.sustained is not False
            or not all(in_period(point.date, week.period) for point in change.workouts)
        ):
            continue
        result.append(control_warning(data, diary, change))
    return result


def control_warning(data: Measurements, diary: dict[date, RecoveryDay], change: PerformanceChange) -> AnalyticsWarning:
    first, last = change.workouts[0], change.workouts[-1]
    improved = change.direction == "improved"
    week = data.current_week
    latest_ids = {point.workout_id for point in change.workouts}
    blocks = [
        episode for episode in data.episodes
        if any(item.workout_id in latest_ids for item in episode.reactions)
    ]
    linked = {item.workout_id: item for block in blocks for item in block.reactions if item.workout_id in latest_ids}
    facts = []
    if improved:
        facts.append(week_fact(week))
        for day in data.recovery_pattern.evaluated_dates:
            facts.extend(
                diary_fact(diary[day], metric) for metric, point in diary[day].metrics.items() if point.delta is not None
            )
    reference_period = DateRange(start=change.baseline_attempts[0].date, end=change.baseline_attempts[-1].date)
    for point in change.workouts:
        reaction = linked.get(point.workout_id)
        facts.append(WarningFact(
            metric="control_mean_power", unit="watts", value=point.mean_watts, baseline=change.baseline_watts,
            baseline_kind="individual", delta=point.mean_watts - change.baseline_watts, delta_pct=point.delta_pct,
            date=point.date, workout_id=point.workout_id, observations=len(change.baseline_attempts),
            reference_period=reference_period, load_date=reaction.load_date if reaction else None,
            lag_days=reaction.lag_days if reaction else None,
        ))
    facts.extend(
        WarningFact(metric="control_baseline_sample", unit="watts", value=point.mean_watts,
                    date=point.date, workout_id=point.workout_id)
        for point in change.baseline_attempts
    )
    load_links = load_refs(
        data, week.period if improved else None, [workout_id for block in blocks for workout_id in block.workout_ids],
    )
    control_ids = latest_ids | set(change.baseline_workout_ids)
    refs = [item for item in load_links if item.workout_id not in control_ids]
    refs.extend(WarningWorkout(workout_id=point.workout_id, date=point.date, role="control_attempt") for point in change.workouts)
    refs.extend(
        WarningWorkout(workout_id=point.workout_id, date=point.date, role="control_baseline")
        for point in change.baseline_attempts
    )
    return AnalyticsWarning(
        id=f"{data.athlete_id}:control:{first.workout_id}:" + ("D" if improved else "C"),
        episode_id=episode_id(data, blocks[0]) if len(blocks) == 1 else None,
        type="positive_adaptation_signs" if improved else "possible_performance_decline",
        title="Признаки положительной адаптации" if improved else "Возможное снижение работоспособности",
        attention="informational" if improved else "moderate",
        period=week.period if improved else DateRange(start=first.date, end=last.date), workouts=refs, facts=facts,
        coverage=(load_coverage(data) if improved else WarningCoverage(status="sufficient")).model_copy(update={
            "performance_baseline_attempts": len(change.baseline_attempts),
            "performance_recent_attempts": len(change.workouts),
        }),
        explanation=(
            "При повышенной подтверждённой недельной нагрузке сопоставимые результаты улучшились. "
            "В доступных записях недели устойчивого ухудшения восстановления не выявлено."
            if improved else "Мощность в повторных контрольных попытках по одному протоколу ниже индивидуальной медианы."
        ),
        limitations=[OBSERVATIONAL, PROTOCOL_LIMIT, RECOVERY_GAPS if improved else SAME_DAY],
        protocol=change.protocol, duration_seconds=change.duration_seconds,
        thresholds={
            "performance_change_pct": change.threshold_pct, "performance_baseline_attempts": data.rules.performance_baseline_attempts,
            "performance_repeat_attempts": data.rules.performance_repeat_attempts,
            **({"above_baseline_pct": data.rules.high_load_pct} if improved else {}),
        },
    )


def missing_data(data: Measurements) -> AnalyticsWarning | None:
    rules, week = data.rules, data.current_week
    start = data.completed_through - (rules.warning_diary_window_days - 1) * DAY
    period = DateRange(start=start, end=data.completed_through)
    missing = [day for day in data.missing_diary_dates if in_period(day, period)]
    filled = rules.warning_diary_window_days - len(missing)
    gaps = []
    if week.status in {"missing", "insufficient"}:
        gaps.append("insufficient_comparable_load_history")
    if data.recovery_pattern.sustained is None:
        gaps.append("insufficient_evaluable_recovery_days")
    if filled < rules.warning_diary_min_entries:
        gaps.append("sparse_diary")
    if not gaps:
        return None
    explanation = [f"За {rules.warning_diary_window_days} завершённых дней заполнено анкет: {filled}."]
    if "insufficient_comparable_load_history" in gaps:
        explanation.append("Для сравнения недельной нагрузки с индивидуальным уровнем мало сопоставимых данных.")
    if "insufficient_evaluable_recovery_days" in gaps:
        explanation.append("Мало дней с несколькими измеренными показателями восстановления и индивидуальной базой.")
    evaluated = len(data.recovery_pattern.evaluated_dates)
    return AnalyticsWarning(
        id=f"{data.athlete_id}:coverage:E", type="insufficient_data", title="Недостаточность данных",
        attention="informational", period=period, workouts=load_refs(data, week.period),
        facts=[
            WarningFact(metric="filled_diary_days", unit="days", period=period, value=filled,
                        baseline=rules.warning_diary_min_entries, baseline_kind="required_minimum",
                        delta=filled - rules.warning_diary_min_entries, observations=rules.warning_diary_window_days),
            WarningFact(metric="known_load_days", unit="days", period=week.period, value=week.coverage.known_days,
                        baseline=rules.weekly_min_days, baseline_kind="required_minimum",
                        delta=week.coverage.known_days - rules.weekly_min_days, observations=week.coverage.days),
            WarningFact(metric="comparable_load_weeks", unit="weeks", period=week.period, value=len(week.baseline_blocks),
                        baseline=rules.weekly_min_baseline_blocks, baseline_kind="required_minimum",
                        delta=len(week.baseline_blocks) - rules.weekly_min_baseline_blocks),
            WarningFact(metric="evaluated_recovery_days", unit="days", period=week.period, value=evaluated,
                        baseline=rules.recovery_min_evaluated_days, baseline_kind="required_minimum",
                        delta=evaluated - rules.recovery_min_evaluated_days, observations=WEEK_DAYS),
        ],
        coverage=load_coverage(data, gaps=gaps).model_copy(update={
            "status": "insufficient", "diary_period": period, "diary_filled_days": filled, "missing_diary_dates": missing,
        }),
        explanation=" ".join(explanation), limitations=[RECOVERY_GAPS, PARTIAL_LOAD],
        thresholds={"diary_min_entries": rules.warning_diary_min_entries, "recovery_min_evaluated_days": rules.recovery_min_evaluated_days},
    )


def normalization_warnings(data: Measurements, diary: dict[date, RecoveryDay], recent_start: date) -> list[AnalyticsWarning]:
    result = []
    for episode in data.episodes:
        normal = episode.normalization_dates
        if not normal or normal[-1] < recent_start:
            continue
        degraded = [item for item in episode.reactions if item.workout_id is None]
        metrics = sorted({item.metric for item in degraded})
        key = episode_id(data, episode)
        period = DateRange(start=episode.period.start, end=normal[-1])
        days = [day for day in data.load if in_period(day.date, episode.period)]
        facts = [
            WarningFact(metric=item.metric, unit="minutes" if item.metric == "sleep_minutes" else "points",
                        value=item.value, baseline=item.baseline, baseline_kind="individual",
                        delta=item.delta, date=item.date, observations=item.observations,
                        load_date=item.load_date, lag_days=item.lag_days)
            if item.date not in diary else diary_fact(diary[item.date], item.metric, item)
            for item in degraded
        ]
        facts.extend(diary_fact(diary[day], metric) for day in normal for metric in metrics)
        exposure = max(episode.exposures, key=lambda item: (item.delta_pct, item.date), default=None)
        if exposure is not None:
            facts.append(WarningFact(
                metric=f"{exposure.kind}_tss", unit="tss", value=exposure.tss, baseline=exposure.baseline_tss,
                baseline_kind="individual", delta=exposure.delta_tss, delta_pct=exposure.delta_pct,
                period=exposure.period, date=exposure.date, observations=exposure.baseline_observations,
            ))
        observed = coverage(days)
        full = (
            observed.confirmed_days == observed.known_days == (episode.period.end - episode.period.start).days + 1
            and exposure is not None and exposure.status == "sufficient"
        )
        status: Status = "sufficient" if full and not episode.open else "partial"
        limits = [OBSERVATIONAL, SAME_DAY, RECOVERY_GAPS]
        if not full:
            limits.append(PARTIAL_LOAD)
        if episode.open:
            limits.append("Семидневное окно наблюдения после последней даты нагрузки ещё открыто.")
        result.append(AnalyticsWarning(
            id=key + ":F", episode_id=key, type="recovery_after_load_block", title="Восстановление после нагрузочного блока",
            attention="informational", period=period, workouts=load_refs(data, episode.period, episode.workout_ids),
            facts=facts, coverage=WarningCoverage(
                status=status, load=observed, load_status="sufficient" if full else "partial", load_exposure=exposure,
                recovery_period=period, evaluated_recovery_dates=normal,
                worsened_recovery_dates=sorted({item.date for item in degraded}),
            ),
            explanation=f"После нагрузочного блока ранее ухудшившиеся показатели вернулись в пределы "
            f"индивидуальной нормы в {len(normal)} наблюдаемых днях.",
            limitations=limits, thresholds={"normalization_days": data.rules.recovery_normalization_days},
        ))
    return result


def athlete_warnings(data: Measurements) -> AthleteWarnings:
    diary = {day.date: day for day in data.recovery if day.date <= data.completed_through}
    recent_start = data.completed_through - (data.rules.warning_recent_days - 1) * DAY
    warnings = [
        *fatigue_and_growth(data, diary),
        *performance_warnings(data, diary, recent_start),
        *normalization_warnings(data, diary, recent_start),
    ]
    coverage_warning = missing_data(data)
    if coverage_warning is not None:
        warnings.append(coverage_warning)
    warnings.sort(key=lambda item: (-ATTENTION_ORDER[item.attention], -item.period.end.toordinal(), item.id))
    groups = defaultdict(list)
    for item in warnings:
        if item.episode_id is not None:
            groups[item.episode_id].append(item.id)
    for item in warnings:
        item.related_warning_ids = [key for key in groups.get(item.episode_id, ()) if key != item.id]
    return AthleteWarnings(
        athlete_id=data.athlete_id, timezone=data.timezone, completed_through=data.completed_through,
        current_week=data.current_week, recovery=data.recovery_pattern, performance_status=data.performance.status,
        max_attention=warnings[0].attention if warnings else None, warnings=warnings,
    )
