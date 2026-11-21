import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import wellness
from app.athlete_models import WellnessEntry, Workout
from app.training_intelligence import routes
from app.workout_models import WorkoutMetrics
from conftest import ORIGIN
from fit_fixtures import START, cycling_fit, magene_wrapper
from test_athletes import API, DIARY, edit_profile, team, write
from test_workout_import import update_ftp, upload

MODULE = "/training-intelligence"


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def test_additive_migration_preserves_existing_diary_rows(migration):
    path, config = migration
    command.upgrade(config, "0005_coach")
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO users (id,email,name,role,active,created_at,updated_at) "
                   "VALUES (11,'athlete@example.com','Synthetic','athlete',1,1,1)")
        db.execute("INSERT INTO athlete_profiles (athlete_id,timezone,created_at,updated_at) VALUES (11,'UTC',1,1)")
        db.execute(
            "INSERT INTO wellness_entries (athlete_id,entry_date,timezone,sleep_quality,general_fatigue,"
            "leg_fatigue,motivation,mood,muscle_soreness,illness_symptoms,comment,created_at,updated_at) "
            "VALUES (11,'2026-01-02','UTC',4,3,2,5,4,1,0,'historic',1,1)"
        )
    command.upgrade(config, "head")
    command.check(config)
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT sleep_quality,comment,sleep_minutes,recovery,training_day_status FROM wellness_entries"
        ).fetchall() == [(4, "historic", None, None, None)]
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE wellness_entries SET recovery=7")
    command.downgrade(config, "0005_coach")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT sleep_quality,comment FROM wellness_entries").fetchall() == [(4, "historic")]


def test_coverage_measurements_respect_day_boundaries_and_historical_ftp(team, monkeypatch):
    (coach, coach_csrf), (owner, athlete_id, csrf), (other, other_id, other_csrf) = team
    timestamp = datetime(2026, 1, 4, 22, tzinfo=UTC).timestamp()
    monkeypatch.setattr(wellness, "time", lambda: timestamp)
    monkeypatch.setattr(routes, "time", lambda: timestamp)
    assert edit_profile(coach, coach_csrf, other_id, timezone="America/New_York").status_code == 204
    assert update_ftp(coach, coach_csrf, athlete_id, "2026-01-02", 250).status_code == 200
    fit = cycling_fit()
    imported = ok(upload(owner, fit, csrf))
    assert ok(upload(owner, magene_wrapper(fit), csrf))["workout_id"] == imported["workout_id"]
    assert ok(upload(other, fit, other_csrf))["status"] == "imported"
    assert update_ftp(coach, coach_csrf, athlete_id, "2026-01-02", 300).status_code == 200

    diary = f"/athletes/{athlete_id}/wellness"
    values = DIARY | {"sleep_minutes": 450, "recovery": 4, "training_day_status": "all_workouts_recorded"}
    assert ok(write(owner, "PUT", diary + "/2026-01-02", values, csrf))["training_day_status"] == "all_workouts_recorded"
    assert ok(write(owner, "PUT", diary + "/2026-01-02", DIARY, csrf))["sleep_minutes"] == 450
    assert ok(write(owner, "PUT", diary + "/2026-01-02", DIARY | {"recovery": None}, csrf))["recovery"] is None
    rest = DIARY | {"training_day_status": "rest"}
    assert write(owner, "PUT", diary + "/2026-01-05", rest, csrf).status_code == 422
    assert ok(write(owner, "PUT", diary + "/2026-01-03", rest, csrf))["training_day_status"] == "rest"
    measures = f"{MODULE}/athletes/{athlete_id}/measurements"
    assert owner.get(API + measures).status_code == 403
    assert other.get(API + measures).status_code == 404
    with TestClient(owner.app, base_url=ORIGIN) as guest:
        assert guest.get(API + measures).status_code == 401
    response = coach.get(API + measures, params={"period": "7"})
    assert response.headers["cache-control"] == "no-store"
    body = ok(response)
    assert body["completed_through"] == "2026-01-04" and body["timezone"] == "Europe/Moscow"
    store, = body["workouts"]
    assert store["date"] == "2026-01-02"
    assert store["if_value"] == 0.8 and store["tss"] == pytest.approx(120 / 3600 * 0.8 ** 2 * 100)
    assert store["best_power"] == {"5": 200, "60": 200, "300": None, "1200": None}
    by_day = {day["date"]: day for day in body["load"]}
    assert by_day["2026-01-02"]["day_status"] == "all_workouts_recorded"
    assert by_day["2026-01-03"]["day_status"] == "rest" and by_day["2026-01-03"]["tss"] == 0
    assert by_day["2026-01-04"]["day_status"] == "unknown" and by_day["2026-01-04"]["tss"] is None
    assert body["current_week"]["coverage"]["confirmed_days"] == 2
    assert body["performance"]["status"] == "insufficient"
    other_measures = ok(coach.get(API + f"{MODULE}/athletes/{other_id}/measurements"))
    assert other_measures["workouts"][0]["date"] == "2026-01-01"
    assert other_measures["workouts"][0]["tss"] is None
    assert other_measures["workouts"][0]["reasons"]["tss"] == "ftp_missing"
    assert coach.get(API + measures, params={"period": "6"}).status_code == 422
    assert len(ok(coach.get(API + measures, params={"period": "42"}))["load"]) == 42

    suspected = ok(upload(owner, cycling_fit(start=START + 1, power=201), csrf))
    assert suspected["potential_duplicates"] == [imported["workout_id"]]
    refreshed = ok(coach.get(API + measures, params={"period": "7"}))
    conflicted = next(day for day in refreshed["load"] if day["date"] == "2026-01-02")
    assert conflicted["day_status"] == "conflict" and conflicted["tss"] is None
    assert all(item["duplicate_candidates"] for item in refreshed["workouts"])


def test_control_efforts_and_config_use_access_checks_and_invalidate_on_fit_update(team, monkeypatch):
    (coach, coach_csrf), (owner, athlete_id, csrf), (_, _, _) = team
    monkeypatch.setattr(routes, "time", lambda: START + 10 * 86400)
    importer = coach.app.state.workout_import
    effort = {"protocol": "erg-standard", "start_seconds": 10, "duration_seconds": 60}
    settings = f"{MODULE}/settings"
    assert owner.get(API + settings).status_code == 403
    assert ok(coach.get(API + settings))["performance_change_pct"] == 5
    assert write(coach, "PUT", settings, {"correlation_min_pairs": 29}, coach_csrf).status_code == 422
    assert ok(write(coach, "PUT", settings, {"performance_change_pct": 8}, coach_csrf))["performance_change_pct"] == 8
    for index, watts in enumerate((190, 200, 210, 180, 180)):
        result = importer.import_fit(
            athlete_id, cycling_fit(start=START + index * 86400, power=watts),
            filename="synthetic.fit", source="onelapfit", external_id=f"effort-{index}",
        )
        path = f"{MODULE}/workouts/{result.workout_id}"
        good = {"Origin": ORIGIN, "X-CSRF-Token": coach_csrf}
        if index == 0:
            assert owner.get(API + path + "/effort").status_code == 403
            assert write(owner, "PUT", path + "/effort", effort, csrf).status_code == 403
            assert write(coach, "PUT", path + "/effort", effort).status_code == 403
            assert coach.put(API + path + "/effort", json=effort, headers=good | {"Origin": "https://elsewhere.test"}).status_code == 403
            assert ok(coach.get(API + path + "/effort")) is None
            assert write(coach, "PUT", path + "/effort", effort | {"duration_seconds": 120}, coach_csrf).status_code == 422
            # Simulate a FIT imported before G2. Use stored blocks and keep the legacy result intact.
            with Session(coach.app.state.engine) as sql:
                metrics = sql.get(WorkoutMetrics, result.workout_id)
                metrics.result = {key: value for key, value in metrics.result.items() if key != "power_projection"}
                sql.commit()
            assert ok(coach.get(API + path + "/best-power"))["60"] == watts
        marked = ok(write(coach, "PUT", path + "/effort", effort, coach_csrf))
        assert marked["mean_watts"] == watts and marked["protocol"] == "erg-standard"

    measures = API + f"{MODULE}/athletes/{athlete_id}/measurements"
    repeated, = ok(coach.get(measures))["performance"]["repeated_changes"]
    assert repeated["direction"] == "decreased" and repeated["threshold_pct"] == 8
    assert [point["delta_pct"] for point in repeated["workouts"]] == [-10, -10]
    assert importer.import_fit(
        athlete_id, cycling_fit(start=START + 4 * 86400, power=190),
        filename="synthetic-revision.fit", source="onelapfit", external_id="effort-4",
    ).status == "updated"
    stale = ok(coach.get(API + path + "/effort"))
    assert stale["mean_watts"] is None and stale["reason"] == "fit_or_algorithm_changed"
    assert ok(coach.get(measures))["performance"]["repeated_changes"] == []
    assert write(coach, "DELETE", path + "/effort", None, coach_csrf).status_code == 204
    assert ok(coach.get(API + path + "/effort")) is None


def test_measurements_link_observed_deterioration_without_mixing_athletes(team, monkeypatch):
    (coach, _), (_, first, _), (_, second, _) = team
    monkeypatch.setattr(routes, "time", lambda: START + 42 * 86400)
    earliest = datetime.fromtimestamp(START + 3 * 3600, tz=UTC).date()
    with Session(coach.app.state.engine) as sql:
        for athlete in (first, second):
            for offset in range(42):
                day = earliest + timedelta(days=offset)
                bad = athlete == first and offset in {36, 37}
                values = DIARY | ({
                    "sleep_quality": 2, "general_fatigue": 5,
                } if bad else {})
                sql.add(WellnessEntry(
                    athlete_id=athlete, entry_date=day, timezone="Europe/Moscow",
                    training_day_status="all_workouts_recorded", **values,
                ))
                ride = Workout(athlete_id=athlete, started_at=START + offset * 86400, timer_seconds=3600)
                sql.add(ride)
                sql.flush()
                usual = 75 if athlete == first else 110
                sql.add(WorkoutMetrics(
                    workout_id=ride.id, version="synthetic", snapshot={}, metadata_json={},
                    result={"tss": usual if offset < 35 else 100},
                ))
        sql.commit()
    first_body, second_body = [
        ok(coach.get(API + f"{MODULE}/athletes/{athlete}/measurements", params={"period": "7"}))
        for athlete in (first, second)
    ]
    assert first_body["current_week"]["delta_pct"] == pytest.approx(100 / 3)
    assert second_body["current_week"]["delta_pct"] == pytest.approx(-100 / 11)
    episode, = first_body["episodes"]
    assert len(episode["reactions"]) == 4
    assert all(item["same_date_only"] for item in episode["reactions"])
    assert first_body["recovery_pattern"]["sustained"] is True
    assert second_body["recovery_pattern"]["sustained"] is False
    assert second_body["episodes"] == []
