from datetime import date, timedelta
from math import exp

import pytest

from app.training_intelligence.load import DAY, daily_load, week_load
from app.training_intelligence.performance import performance_trend
from app.training_intelligence.power import best_power, interval_power
from app.training_intelligence.recovery import recovery_days, recovery_pattern
from app.training_intelligence.relationships import episodes, lag_correlations
from app.training_intelligence.schemas import EffortPoint, Reaction, Rules, WorkoutLoad

START = date(2026, 1, 1)


def workout(number, day, tss, *, candidates=()):
    return WorkoutLoad(
        workout_id=number, date=day, started_at=0, timer_seconds=1200, np=200, if_value=0.9,
        tss=tss, intense=True, rpe=0, session_rpe=0, duplicate_candidates=list(candidates),
    )


def test_smoothing_rest_unknown_and_partial_tss_have_distinct_meanings():
    today = START + 7 * DAY
    rides = [
        workout(1, START, 100), workout(2, START + 3 * DAY, 60), workout(3, START + 3 * DAY, None),
        workout(4, START + 4 * DAY, 80, candidates=(5,)), workout(5, START + 4 * DAY, 80, candidates=(4,)),
        workout(6, START + 5 * DAY, 80), workout(7, today, 40),
    ]
    days = daily_load(START, today, rides, {
        START + 2 * DAY: "rest", START + 3 * DAY: "all_workouts_recorded",
        START + 5 * DAY: "rest", START + 6 * DAY: "all_workouts_recorded", today: "all_workouts_recorded",
    }, Rules())
    assert (days[0].atl, days[0].ctl, days[0].tsb, days[0].tsb_status) == (100, 100, 0, "insufficient")
    assert days[0].session_rpe == 0
    assert days[1].day_status == "unknown" and days[1].tss is days[1].atl is None
    assert days[2].day_status == "rest" and days[2].tss == 0
    expected = [100 * exp(-2 / period) / (exp(-2 / period) + 1 - exp(-1 / period)) for period in (7, 42)]
    assert [days[2].atl, days[2].ctl, days[2].tsb] == pytest.approx([*expected, expected[1] - expected[0]])
    assert days[2].ctl_coverage.known_days == 2 and days[2].ctl_coverage.confirmed_days == 1
    assert days[3].recorded_tss == 60 and days[3].tss is None
    assert [day.day_status for day in days[4:7]] == ["conflict"] * 3
    assert all(day.tss is None for day in days[4:7])
    assert days[-1].preliminary and days[-1].coverage.confirmed_days == 0


def test_full_coverage_and_individual_weekly_deviations():
    today = START + 49 * DAY
    rules = Rules()
    confirmed = {START + i * DAY: "all_workouts_recorded" for i in range(49)}
    high, normal = [
        daily_load(
            START, today,
            [workout(i, START + i * DAY, baseline if i < 42 else 100) for i in range(49)],
            confirmed, rules,
        ) for baseline in (75, 110)
    ]
    assert high[48].tsb_status == "sufficient"
    assert high[48].elevated_streak == 7 and normal[48].elevated_streak == 0
    higher, lower = [week_load(days, 48, rules) for days in (high, normal)]
    assert higher.delta_pct == pytest.approx(100 / 3)
    assert lower.delta_pct == pytest.approx(-100 / 11)
    assert higher.recorded_tss == 700 and higher.baseline_tss == 525
    assert len(higher.baseline_blocks) == 4 and higher.status == "sufficient"
    assert higher.baseline_blocks[0].coverage.known_days == 7


def test_weekly_comparison_marks_partial_coverage_and_rejects_mismatched_windows():
    today = START + 35 * DAY
    rules = Rules()
    rides = [workout(i, START + i * DAY, 136 if i >= 28 else 102) for i in range(35) if i % 7 < 5]
    partial = week_load(daily_load(START, today, rides, {}, rules), 34, rules)
    assert partial.status == "partial"
    assert partial.coverage.known_days == 5 and partial.coverage.confirmed_days == 0
    assert partial.comparable_tss == 680 and partial.baseline_tss == 510
    assert partial.delta_pct == pytest.approx(100 / 3)
    assert partial.change_from_previous_pct == pytest.approx(100 / 3)
    rides.extend(workout(i, START + i * DAY, 136) for i in (33, 34))
    insufficient = week_load(daily_load(START, today, rides, {}, rules), 34, rules)
    assert insufficient.status == "insufficient" and insufficient.delta_pct is None


def test_recovery_uses_prior_observations_and_independent_signal_groups():
    first = START + 30 * DAY
    entries = {first - offset * DAY: {
        "sleep_quality": 4, "sleep_minutes": 450, "general_fatigue": 2, "leg_fatigue": 1,
    } for offset in (1, 2, 4, 7, 21, 23, 28, 29)}
    entries[first - 29 * DAY] = {"sleep_minutes": 500}
    entries.update({
        first: {"sleep_quality": 2, "sleep_minutes": 400, "general_fatigue": 4},
        first + DAY: {"sleep_quality": 2, "sleep_minutes": 400, "general_fatigue": 4},
        first + 3 * DAY: {"sleep_quality": 2, "sleep_minutes": 400, "general_fatigue": 2},
        first + 4 * DAY: {"sleep_quality": 4, "sleep_minutes": 450, "general_fatigue": 2},
    })
    days = recovery_days(first, first + 6 * DAY, entries, Rules())
    assert days[0].metrics["sleep_minutes"].baseline == 450
    assert days[0].metrics["sleep_minutes"].observations == 7
    assert days[0].metrics["sleep_minutes"].delta == -50
    assert days[0].metrics["recovery"].status == "missing"
    assert days[2].metrics["sleep_minutes"].value is None and days[2].metrics["sleep_minutes"].delta is None
    trend = recovery_pattern(days)
    assert trend.sustained is True and trend.deterioration_dates == [first, first + DAY]
    assert recovery_pattern(days[:3]).sustained is None


def effort(index, watts, *, protocol="erg-5min", duration=300, offset=None, rpe=None):
    return EffortPoint(
        workout_id=index + 1, date=START + (index if offset is None else offset) * DAY,
        protocol=protocol, start_seconds=0, duration_seconds=duration, mean_watts=watts, rpe=rpe,
        status="sufficient" if watts is not None else "insufficient",
    )


@pytest.mark.parametrize(("new_power", "direction"), [(180, "decreased"), (220, "improved")])
def test_two_new_attempts_share_three_prior_attempts_in_one_protocol(new_power, direction):
    attempts = [effort(i, power) for i, power in enumerate((190, 200, 210, new_power, new_power))]
    attempts.extend((effort(5, 400, protocol="outside"), effort(6, 400, duration=301)))
    points, changes, _ = performance_trend(attempts, START + 7 * DAY, Rules())
    change, = changes
    assert change.direction == direction and change.baseline_watts == 200
    assert change.baseline_workout_ids == [1, 2, 3]
    assert [item.workout_id for item in change.workouts] == [4, 5]
    assert [item.delta_pct for item in change.workouts] == [100 * (new_power - 200) / 200] * 2
    assert points[-1].reason == points[-2].reason == "too_few_comparable_attempts"
    assert not performance_trend(attempts, START + 7 * DAY, Rules(performance_change_pct=12))[1]
    assert not performance_trend(attempts[1:], START + 7 * DAY, Rules())[1]


def test_repeated_performance_rejects_mixed_directions_same_day_and_stale_measurements():
    attempts = [effort(i, power) for i, power in enumerate((200, 200, 200, 180, 220))]
    assert not performance_trend(attempts, START + 4 * DAY, Rules())[1]
    same_day = attempts[:-1] + [effort(4, 180, offset=3)]
    assert not performance_trend(same_day, START + 4 * DAY, Rules())[1]
    assert not performance_trend(attempts[:-1] + [effort(4, None)], START + 4 * DAY, Rules())[1]
    rpe_samples = [effort(i, 200, rpe=rpe) for i, rpe in enumerate((2, 3, 4, 0))]
    point = performance_trend(rpe_samples, START + 4 * DAY, Rules())[0][-1]
    assert point.rpe == 0 and point.baseline_rpe == 3 and point.rpe_observations == 3


def test_power_windows_preserve_zero_and_do_not_bridge_pause_or_gap():
    observations = [(0, {"power": 0}, 2, True), (2, {"power": 200}, 3, False),
                    (5, {"power": 500}, 4, True), (10, {"power": 400}, 4, True)]
    assert best_power(observations) == {"5": 120, "60": None, "300": None, "1200": None}
    assert interval_power(observations, 0, 2) == 0
    assert interval_power(observations, 1, 4) == 150
    assert interval_power(observations, 1, 6) is None
    assert interval_power(observations, 5, 6) is None
    assert interval_power(observations, 9, 6) is None


def test_episodes_merge_overlaps_and_each_reaction_uses_nearest_exposure():
    exposures = [START, START + 5 * DAY, START + 13 * DAY]
    load = daily_load(START, START + 30 * DAY, [workout(i, day, 100) for i, day in enumerate(exposures, 1)], {}, Rules())
    reactions = [
        Reaction(date=day, metric="sleep_quality", value=2, baseline=4, delta=-2)
        for day in (START, START + DAY, START + 7 * DAY, START + 12 * DAY, START + 13 * DAY, START + 17 * DAY)
    ]
    first, second = episodes([load[(day - START).days] for day in exposures], reactions + reactions, {}, START + 17 * DAY)
    assert first.elevated_dates == exposures[:2] and first.workout_ids == [1, 2]
    assert [item.lag_days for item in first.reactions] == [0, 1, 2, 7]
    assert [item.lag_group for item in second.reactions] == ["0", "4-7"]
    assert first.reactions[0].same_date_only and second.open and not first.open
    assert sum(len(item.reactions) for item in (first, second)) == len(reactions)


def test_episode_normalization_requires_two_observed_days():
    exposure = START + 28 * DAY
    load = daily_load(exposure, exposure + 7 * DAY, [workout(1, exposure, 100)], {}, Rules())
    entries = {exposure - offset * DAY: {"sleep_quality": 4} for offset in range(1, 20)}
    entries.update({exposure + DAY: {"sleep_quality": 2}, exposure + 2 * DAY: {"sleep_quality": 4},
                    exposure + 4 * DAY: {"sleep_quality": 4}})
    recovery = recovery_days(exposure, exposure + 4 * DAY, entries, Rules())
    reaction = Reaction(date=exposure + DAY, metric="sleep_quality", value=2, baseline=4, delta=-2)
    result, = episodes(load[:1], [reaction], {point.date: point for point in recovery}, exposure + 4 * DAY)
    assert result.normalization_dates == [exposure + 2 * DAY, exposure + 4 * DAY]


def test_lag_correlation_does_not_calculate_on_small_samples_or_constant_series():
    load = {START + i * DAY: i for i in range(40)}
    outcomes = {
        "variable": {START + (i + 3) * DAY: i // 2 for i in range(40)},
        "insufficient": {START + i * DAY: i for i in range(29)},
        "constant": dict.fromkeys(load, 1),
        "unstable": {START + i * DAY: i if i < 20 else 59 - i for i in range(40)},
    }
    found = {(item.metric, item.lag_days): item for item in
             lag_correlations(load, set(load), outcomes, START + timedelta(days=42), Rules())}
    stable = found["variable", 3]
    assert stable.pairs == 40 and stable.confirmed_load_pairs == 40
    assert stable.coefficient == pytest.approx(0.99906, abs=0.0001)
    assert stable.stable and stable.status == "sufficient"
    assert found["insufficient", 0].coefficient is None
    assert found["constant", 0].coefficient is None
    assert found["unstable", 0].stable is False and found["unstable", 0].status == "partial"
    partial = lag_correlations(load, set(), {"variable": outcomes["variable"]}, START + 42 * DAY, Rules())[3]
    assert partial.stable and partial.status == "partial" and partial.confirmed_load_pairs == 0
