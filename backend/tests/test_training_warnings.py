from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.athlete_models import AthleteProfile, WellnessEntry, Workout
from app.training_intelligence import routes
from app.training_intelligence.power import POWER_PROJECTION_VERSION
from app.training_intelligence.schemas import Rules
from app.training_intelligence.service import analysis_window, calculate_measurements
from app.training_intelligence.warnings import athlete_warnings
from app.workout_models import WorkoutEffort, WorkoutMetrics
from conftest import ORIGIN
from test_athletes import API, DIARY, edit_profile, team, write

START = date(2026, 1, 1)
DAY = timedelta(days=1)
AS_OF = datetime.combine(START + 42 * DAY, datetime.min.time(), UTC).timestamp()
MODULE = "/training-intelligence"
A = "possible_accumulated_fatigue"
B = "load_growth_with_worsening_recovery"
C = "possible_performance_decline"
D = "positive_adaptation_signs"
E = "insufficient_data"
F = "recovery_after_load_block"


def synthetic_rows(
    athlete_id, *, latest_tss=100, load_override=None, bad=(), sleep_only=(),
    omit_diary=(), omit_workouts=(), unconfirmed=(), attempts=None,
):
    rows = []
    diary = []
    for index in range(42):
        day = START + index * DAY
        if index not in omit_diary:
            scores = DIARY | {"sleep_minutes": 450, "general_fatigue": 2}
            if index in bad or index in sleep_only:
                scores.update(sleep_quality=2, sleep_minutes=390)
            if index in bad:
                scores["general_fatigue"] = 4
            diary.append(WellnessEntry(
                athlete_id=athlete_id, entry_date=day, timezone="UTC",
                training_day_status=None if index in unconfirmed else "all_workouts_recorded", **scores,
            ))
        if index in omit_workouts:
            continue
        workout = Workout(
            id=index + 1, athlete_id=athlete_id, started_at=int(AS_OF - (42 - index) * 86400),
            timer_seconds=3600, file_sha256=f"{index:064x}",
        )
        amount = (load_override or {}).get(index, latest_tss if index >= 35 else 100)
        effort = None
        if index in (attempts or {}):
            watts, protocol, duration = attempts[index]
            effort = WorkoutEffort(
                workout_id=workout.id, protocol=protocol, start_seconds=10, duration_seconds=duration,
                file_sha256=workout.file_sha256 if watts is not None else "stale",
                projection_version=POWER_PROJECTION_VERSION, mean_watts=0 if watts is None else watts,
            )
        rows.append((workout, None, {"tss": amount}, effort))
    return rows, diary


def analyze(*, rules=None, **inputs):
    rules = rules or Rules()
    profile = AthleteProfile(athlete_id=1, timezone="UTC")
    rows, diary = synthetic_rows(1, **inputs)
    window = analysis_window(profile, 45, AS_OF, rules)
    data = calculate_measurements(profile, window, rows, diary, rules, [], for_warnings=True)
    overview = athlete_warnings(data)
    return overview, {item.type: item for item in overview.warnings}


def test_fatigue_and_growth_share_episode_and_expose_exact_evidence():
    overview, found = analyze(latest_tss=120, bad=(39, 40, 41))
    assert set(found) == {A, B}
    fatigue, growth = found[A], found[B]
    assert overview.max_attention == fatigue.attention == "high"
    assert growth.attention == "moderate"
    assert fatigue.episode_id == growth.episode_id
    assert fatigue.related_warning_ids == [growth.id] and growth.related_warning_ids == [fatigue.id]
    week, *recovery = fatigue.facts
    assert (week.value, week.baseline, week.delta, week.delta_pct, week.unit) == (840, 700, 140, 20, "tss")
    assert len(fatigue.coverage.load_reference_blocks) == 4
    assert fatigue.coverage.load.confirmed_days == 7
    assert all(fact.date and fact.observations and fact.lag_days == 0 for fact in recovery)
    assert len(fatigue.workouts) == 7
    assert growth.facts[0].reference_period.end == fatigue.period.start - DAY
    worsened = next(fact for fact in growth.facts if fact.metric == "days_with_multiple_worsening_metrics")
    assert (worsened.baseline, worsened.value) == (0, 3)
    assert analyze(latest_tss=120, bad=(39, 40, 41))[1][A].id == fatigue.id


def test_fatigue_attention_and_coverage_thresholds():
    assert analyze(latest_tss=120, bad=(40, 41))[1][A].attention == "moderate"
    partial = analyze(latest_tss=130, bad=(39, 40, 41), unconfirmed=(35,))[1][A]
    assert partial.attention == "moderate" and partial.coverage.status == "partial"
    assert partial.coverage.load.confirmed_days == 6
    assert A not in analyze(latest_tss=120, bad=(39, 40, 41), rules=Rules(high_load_pct=20.01))[1]
    conflicted = analyze(latest_tss=140, bad=(39, 40, 41), omit_workouts=(35,))[1]
    assert E in conflicted and A not in conflicted and B not in conflicted
    assert A not in analyze(latest_tss=140)[1]
    assert A not in analyze(latest_tss=140, sleep_only=(39, 40, 41))[1]


def test_fatigue_links_delayed_recovery_to_one_merged_episode():
    overview, warnings = analyze(load_override={35: 150, 36: 150, 37: 150}, bad=(38, 39, 40))
    fatigue = warnings[A]
    assert len([warning for warning in overview.warnings if warning.type == A]) == 1
    assert {fact.lag_days for fact in fatigue.facts[1:]} == {1, 2, 3}
    assert {fact.load_date for fact in fatigue.facts[1:]} == {START + 37 * DAY}


def test_growth_may_be_below_individual_high_load_and_requires_late_worsening():
    found = analyze(latest_tss=115, bad=(39, 40))[1]
    assert set(found) == {B}
    assert found[B].facts[0].delta_pct == 15
    assert found[B].episode_id is None
    assert B not in analyze(latest_tss=115, bad=(39, 40), rules=Rules(warning_growth_pct=15.01))[1]
    assert B not in analyze(latest_tss=115, bad=(35, 36))[1]
    assert B not in analyze(latest_tss=115, bad=(30, 31, 39, 40))[1]
    assert B not in analyze(latest_tss=115, bad=(39, 40), omit_diary=(28, 29, 30, 31))[1]
    short = analyze(latest_tss=115, bad=(39, 40), omit_workouts=range(28))[1]
    assert set(short) == {B, E}
    assert short[B].coverage.status == "sufficient"
    assert "insufficient_comparable_load_history" in short[E].coverage.gaps


def control_attempts(recent_power, *, last_protocol="erg5", last_duration=300, indices=(10, 20, 30, 38, 40)):
    return {
        index: (power, last_protocol if position == 4 else "erg5", last_duration if position == 4 else 300)
        for position, (index, power) in enumerate(zip(indices, (190, 200, 210, recent_power, recent_power), strict=True))
    }


def test_decline_uses_protocol_matched_repeated_attempts_and_all_base_evidence():
    result = analyze(attempts=control_attempts(190))[1][C]
    assert result.attention == "moderate" and result.protocol == "erg5" and result.duration_seconds == 300
    assert result.coverage.performance_baseline_attempts == 3
    assert result.coverage.performance_recent_attempts == 2
    assert [item.role for item in result.workouts] == ["control_attempt"] * 2 + ["control_baseline"] * 3
    changed = [fact for fact in result.facts if fact.metric == "control_mean_power"]
    assert [fact.delta_pct for fact in changed] == [-5, -5]
    assert all(fact.baseline == 200 and fact.delta == -10 and fact.date for fact in changed)
    for attempts in (control_attempts(190, last_protocol="road"), control_attempts(190, last_duration=301),
                     control_attempts(None), control_attempts(190, indices=(3, 5, 7, 9, 10))):
        assert C not in analyze(attempts=attempts)[1]
    four = control_attempts(190)
    del four[10]
    assert C not in analyze(attempts=four)[1]
    assert C not in analyze(attempts=control_attempts(190), rules=Rules(performance_change_pct=5.01))[1]
    assert C not in analyze(attempts=control_attempts(190), rules=Rules(performance_repeat_attempts=3))[1]
    assert C not in analyze(attempts=control_attempts(190), rules=Rules(performance_baseline_attempts=4))[1]


def test_adaptation_requires_confirmed_load_evaluable_diary_and_same_week_improvement():
    ready = {"latest_tss": 120, "attempts": control_attempts(210)}
    found = analyze(**ready)[1]
    assert set(found) == {D}
    assert found[D].attention == "informational"
    assert found[D].coverage.load.confirmed_days == 7
    assert found[D].facts[0].delta_pct == 20
    assert D not in analyze(**ready, unconfirmed=(35,))[1]
    assert D not in analyze(**ready, bad=(39, 40))[1]
    assert D not in analyze(**ready, omit_diary=(35, 36, 37, 39))[1]
    assert D not in analyze(latest_tss=120, attempts=control_attempts(210, indices=(5, 15, 25, 34, 40)))[1]
    assert D not in analyze(latest_tss=119.9, attempts=ready["attempts"])[1]


def test_missing_data_reports_actual_counts_and_quietly_handles_no_observations():
    absent = analyze(omit_workouts=range(42), omit_diary=range(42))[1]
    assert set(absent) == {E}
    result = absent[E]
    assert result.coverage.diary_filled_days == 0 and len(result.coverage.missing_diary_dates) == 14
    assert set(result.coverage.gaps) == {
        "sparse_diary", "insufficient_comparable_load_history", "insufficient_evaluable_recovery_days",
    }
    assert result.facts[0].value == 0 and result.facts[0].observations == 14
    seven = {"omit_diary": range(28, 35)}
    assert E not in analyze(**seven)[1]
    raised = analyze(**seven, rules=Rules(warning_diary_min_entries=8))[1][E]
    assert raised.coverage.gaps == ["sparse_diary"] and raised.coverage.diary_filled_days == 7


def test_recovery_returns_after_load_requires_observed_days_and_shares_no_active_fatigue():
    base = {"load_override": {33: 300, 34: 300}, "bad": (35, 36)}
    found = analyze(**base)[1]
    assert set(found) == {F}
    restored = found[F]
    assert restored.attention == "informational"
    assert restored.coverage.evaluated_recovery_dates == [START + 37 * DAY, START + 38 * DAY]
    assert restored.episode_id and restored.workouts
    assert all(fact.baseline is not None and fact.delta is not None for fact in restored.facts)
    assert restored.coverage.load_exposure is not None and restored.coverage.load_exposure.delta_pct > 20
    assert F not in analyze(**base, omit_diary=(38, 39, 40, 41))[1]
    assert F not in analyze(**base, omit_diary=(39, 40, 41), rules=Rules(recovery_normalization_days=3))[1]
    assert F not in analyze(**base, rules=Rules(warning_recent_days=2))[1]
    recovered_while_week_still_high = analyze(load_override={35: 155, 36: 155, 37: 155}, bad=(36, 37))[1]
    assert set(recovered_while_week_still_high) == {F}


def test_warning_api_access_scope_team_sort_and_settings(team, monkeypatch):
    (coach, coach_csrf), (owner, first_id, csrf), (other, second_id, _) = team
    monkeypatch.setattr(routes, "time", lambda: AS_OF)
    assert edit_profile(coach, coach_csrf, first_id, name="Яна", timezone="UTC").status_code == 204
    assert edit_profile(coach, coach_csrf, second_id, name="Анна", timezone="America/New_York").status_code == 204
    with Session(coach.app.state.engine) as db:
        rows, diary = synthetic_rows(first_id, latest_tss=120, bad=(39, 40, 41))
        for workout, _, metric, _ in rows:
            workout.id = None
            db.add(workout)
            db.flush()
            db.add(WorkoutMetrics(
                workout_id=workout.id, version="synthetic", snapshot={}, result=metric, metadata_json={},
            ))
        db.add_all(diary)
        db.commit()
    team_path = API + MODULE + "/team"
    own_path = API + MODULE + "/me"
    warnings_path = API + MODULE + f"/athletes/{first_id}/warnings"
    response = coach.get(team_path)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    first, second = response.json()["athletes"]
    assert [first["athlete_id"], second["athlete_id"]] == [first_id, second_id]
    assert [first["max_attention"], second["max_attention"]] == ["high", "informational"]
    assert first["warnings"] == coach.get(warnings_path).json()["warnings"]
    assert [item["type"] for item in second["warnings"]] == [E]
    assert second["completed_through"] != first["completed_through"]
    assert owner.get(team_path).status_code == 403 and owner.get(warnings_path).status_code == 403
    assert other.get(warnings_path).status_code == 404
    assert coach.get(own_path).status_code == 403
    assert owner.get(own_path, params={"athlete_id": second_id}).json()["athlete_id"] == first_id
    owner_body = other.get(own_path).json()
    assert owner_body["athlete_id"] == second_id and "warnings" not in owner_body
    with TestClient(coach.app, base_url=ORIGIN) as guest:
        assert [guest.get(path).status_code for path in (team_path, warnings_path, own_path)] == [401, 401, 401]
    settings = MODULE + "/settings"
    assert write(owner, "PUT", settings, {"warning_growth_pct": 40}, csrf).status_code == 403
    assert write(coach, "PUT", settings, {"performance_baseline_attempts": 2}, coach_csrf).status_code == 422
    assert write(coach, "PUT", settings, {"performance_repeat_attempts": 1}, coach_csrf).status_code == 422
    assert write(coach, "PUT", settings, {"warning_growth_pct": 40}).status_code == 403
    assert write(coach, "PUT", settings, {"high_load_pct": 21, "warning_growth_pct": 21}, coach_csrf).status_code == 200
    assert coach.get(warnings_path).json()["warnings"] == []
