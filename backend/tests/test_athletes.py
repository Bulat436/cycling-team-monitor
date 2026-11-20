import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import athletes, wellness
from app.athlete_models import RpeEntry, WellnessEntry, Workout
from conftest import COACH_EMAIL, ORIGIN

API = "/api/v1"
DIARY = {
    "sleep_quality": 4, "general_fatigue": 3, "leg_fatigue": 2,
    "motivation": 5, "mood": 4, "muscle_soreness": 1,
    "illness_symptoms": False, "comment": "Всё по плану",
}


def write(client, method, path, body, csrf=None):
    headers = {"Origin": ORIGIN}
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    return client.request(method, API + path, json=body, headers=headers)


def sign_in(client, email, password):
    response = write(client, "POST", "/auth/login", {"email": email, "password": password})
    assert response.status_code == 200, response.text
    session = response.json()
    return session["user"]["id"], session["csrf_token"]


def invite(coach, csrf, athlete, email, password):
    response = write(coach, "POST", "/admin/invitations", {"name": email, "email": email}, csrf)
    assert response.status_code == 201, response.text
    token = response.json()["token"]
    assert write(athlete, "POST", "/auth/access/accept", {"token": token, "password": password}).status_code == 204
    return sign_in(athlete, email, password)


def edit_profile(coach, csrf, athlete_id, **changes):
    profile = {
        "name": "Саша Велосипедов", "birth_date": "2000-08-08", "height_cm": 178.5,
        "specialization": "Шоссе", "team_group": "Группа А", "timezone": "Europe/Moscow",
        "coach_notes": "Внутренняя заметка",
    }
    return write(coach, "PUT", f"/athletes/{athlete_id}", profile | changes, csrf)


@pytest.fixture
def team(client, password):
    _, coach_csrf = sign_in(client, COACH_EMAIL, password)
    with TestClient(client.app, base_url=ORIGIN) as first, TestClient(client.app, base_url=ORIGIN) as second:
        one = invite(client, coach_csrf, first, "one@example.com", password)
        two = invite(client, coach_csrf, second, "two@example.com", password)
        yield (client, coach_csrf), (first, *one), (second, *two)


def test_stage_a_migration_backfills_existing_athletes(migration):
    path, config = migration
    command.upgrade(config, "0001_access")
    with sqlite3.connect(path) as db:
        db.executemany(
            "INSERT INTO users (id, email, name, role, active, created_at, updated_at) VALUES (?, ?, ?, ?, 1, 1, 1)",
            [(10, "coach@example.com", "Coach", "coach"), (11, "athlete@example.com", "Athlete", "athlete")],
        )
    command.upgrade(config, "head")
    command.check(config)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT athlete_id, timezone FROM athlete_profiles").fetchall() == [(11, "Europe/Moscow")]
        assert db.execute("SELECT count(*) FROM users").fetchone() == (2,)


def test_profile_permissions_and_history_dates(team):
    (coach, coach_csrf), (owner, athlete_id, owner_csrf), (other, other_id, _) = team
    path = f"/athletes/{athlete_id}"
    assert edit_profile(coach, coach_csrf, athlete_id, timezone="No/SuchZone").status_code == 422
    assert edit_profile(coach, coach_csrf, athlete_id).status_code == 204
    assert edit_profile(owner, owner_csrf, athlete_id).status_code == 403
    assert other.get(API + path).status_code == 404
    assert owner.get(API + f"/athletes/{other_id}/ftp").status_code == 404
    assert owner.get(API + "/athletes").status_code == 403

    for date, watts in (("2020-01-01", 225), ("2021-01-01", 245), ("2199-01-01", 350)):
        assert write(coach, "PUT", path + f"/ftp/{date}", {"watts": watts}, coach_csrf).status_code == 200
    assert write(coach, "PUT", path + "/ftp/2021-01-01", {"watts": 246}, coach_csrf).status_code == 200
    assert write(owner, "PUT", path + "/ftp/2020-01-01", {"watts": 250}, owner_csrf).status_code == 403

    weight_path = path + "/weight"
    for date, kg in (("2021-01-01", 72.3), ("2022-01-01", 71.2), ("2022-01-01", 71.25)):
        last = write(coach, "POST", weight_path, {"measured_on": date, "kg": kg}, coach_csrf)
        assert last.status_code == 201, last.text
    record_id = last.json()["id"]
    measurements = {"measured_on": "2022-01-01", "kg": 71.5}
    edited = write(coach, "PUT", weight_path + f"/{record_id}", measurements, coach_csrf)
    assert edited.status_code == 200 and edited.json()["created_at"] == last.json()["created_at"]
    assert write(coach, "PUT", f"/athletes/{other_id}/weight/{record_id}", measurements, coach_csrf).status_code == 404
    assert write(coach, "POST", weight_path, {"measured_on": "2199-01-01", "kg": 73}, coach_csrf).status_code == 422
    assert write(coach, "POST", weight_path, measurements).status_code == 403

    detail = owner.get(API + path).json()
    assert detail["name"] == "Саша Велосипедов"
    assert detail["height_cm"] == 178.5 and detail["birth_date"] == "2000-08-08"
    assert detail["coach_notes"] is None
    assert detail["current_ftp"]["watts"] == 246
    assert detail["current_weight"]["kg"] == 71.5
    assert detail["indicators_updated_at"] is not None
    assert [row["watts"] for row in detail["ftp_history"]] == [350, 246, 225]
    assert [row["kg"] for row in detail["weight_history"]] == [71.5, 71.2, 72.3]
    assert coach.get(API + path).json()["coach_notes"] == "Внутренняя заметка"
    assert len(coach.get(API + "/athletes").json()) == 2


def test_zones_validation_clear_and_permissions(team):
    (coach, coach_csrf), (owner, athlete_id, owner_csrf), (other, _, _) = team
    power = f"/athletes/{athlete_id}/zones/power"
    pulse = f"/athletes/{athlete_id}/zones/heart-rate"
    ranges = [
        {"name": "Восстановление", "minimum": 0, "maximum": 150},
        {"name": "Рабочая", "minimum": 150, "maximum": None},
    ]
    assert write(coach, "PUT", power, {"zones": ranges}, coach_csrf).status_code == 200
    assert owner.get(API + power).json() == {"zones": ranges}
    assert write(owner, "PUT", power, {"zones": []}, owner_csrf).status_code == 403
    assert other.get(API + power).status_code == 404
    overlap = [ranges[0], ranges[1] | {"minimum": 149}]
    assert write(coach, "PUT", power, {"zones": overlap}, coach_csrf).status_code == 422
    assert write(coach, "PUT", pulse, {"zones": [ranges[1] | {"minimum": 301}]}, coach_csrf).status_code == 422
    assert write(coach, "PUT", pulse, {"zones": ranges}, coach_csrf).status_code == 200
    assert write(coach, "PUT", pulse, {"zones": []}, coach_csrf).status_code == 200
    assert owner.get(API + pulse).json() == {"zones": []}
    assert owner.get(API + power).json() == {"zones": ranges}


def test_wellness_uses_each_athletes_calendar_and_preserves_history(team, monkeypatch):
    (coach, coach_csrf), (owner, athlete_id, owner_csrf), (other, other_id, _) = team
    timestamp = datetime(2026, 1, 1, 11, 30, tzinfo=UTC).timestamp()
    monkeypatch.setattr(athletes, "time", lambda: timestamp)
    monkeypatch.setattr(wellness, "time", lambda: timestamp)
    assert edit_profile(coach, coach_csrf, athlete_id, timezone="Pacific/Kiritimati").status_code == 204
    assert edit_profile(coach, coach_csrf, other_id, timezone="Pacific/Honolulu").status_code == 204
    base = f"/athletes/{athlete_id}/wellness"
    path = base + "/2026-01-02"
    assert owner.get(API + base).json()["local_date"] == "2026-01-02"
    assert other.get(API + f"/athletes/{other_id}/wellness").json()["local_date"] == "2026-01-01"
    assert owner.get(API + path).json() is None
    assert write(owner, "PUT", base + "/2026-01-03", DIARY, owner_csrf).status_code == 422
    assert write(owner, "PUT", path, DIARY | {"sleep_quality": 0}, owner_csrf).status_code == 422
    assert write(owner, "PUT", path, DIARY | {"illness_symptoms": 1}, owner_csrf).status_code == 422
    assert write(coach, "PUT", path, DIARY, coach_csrf).status_code == 403
    assert write(owner, "PUT", path, DIARY).status_code == 403
    assert other.get(API + path).status_code == 404
    assert owner.get(API + "/wellness/team").status_code == 403

    first = write(owner, "PUT", path, DIARY, owner_csrf).json()
    assert first["created_at"] == first["updated_at"] == timestamp
    timestamp += 0.5
    changed = write(owner, "PUT", path, DIARY | {"illness_symptoms": True, "comment": None}, owner_csrf).json()
    assert changed["created_at"] == first["created_at"]
    assert changed["updated_at"] == timestamp
    assert changed["illness_symptoms"] and changed["comment"] is None
    assert write(owner, "PUT", base + "/2026-01-01", DIARY, owner_csrf).status_code == 200

    summary = {row["athlete_id"]: row for row in coach.get(API + "/wellness/team").json()}
    assert summary[athlete_id]["entry"]["entry_date"] == "2026-01-02"
    assert summary[other_id]["entry"] is None
    history = coach.get(API + base + "?limit=1").json()
    assert history["has_more"] and len(history["entries"]) == 1
    assert coach.get(API + base + "?limit=1&offset=1").json()["entries"][0]["entry_date"] == "2026-01-01"

    assert edit_profile(coach, coach_csrf, athlete_id, timezone="Pacific/Honolulu").status_code == 204
    assert owner.get(API + base).json()["today_entry"]["entry_date"] == "2026-01-01"
    assert owner.get(API + path).json()["timezone"] == "Pacific/Kiritimati"
    assert write(owner, "PUT", path, DIARY, owner_csrf).status_code == 200


def test_concurrent_diary_writes_do_not_create_two_entries(team):
    (_, _), (owner, athlete_id, csrf), (_, _, _) = team
    base = f"/athletes/{athlete_id}/wellness"
    today = owner.get(API + base).json()["local_date"]

    def submit(mood):
        with TestClient(owner.app, base_url=ORIGIN, cookies=owner.cookies) as browser:
            return write(browser, "PUT", base + "/" + today, DIARY | {"mood": mood}, csrf).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(submit, [1, 5])) == [200, 200]
    with Session(owner.app.state.engine) as db:
        assert db.scalar(select(func.count()).select_from(WellnessEntry)) == 1


def test_rpe_per_workout_supports_zero_and_missing_duration(team):
    (coach, coach_csrf), (owner, athlete_id, csrf), (other, other_id, other_csrf) = team
    with Session(owner.app.state.engine, expire_on_commit=False) as db:
        morning = Workout(athlete_id=athlete_id, started_at=1767250800, timer_seconds=3600)
        evening = Workout(athlete_id=athlete_id, started_at=1767272400, timer_seconds=None)
        foreign = Workout(athlete_id=other_id, started_at=1767272400, timer_seconds=1800)
        db.add_all([morning, evening, foreign])
        db.commit()

    workout_list = f"/athletes/{athlete_id}/workouts"
    route = f"/workouts/{morning.id}/rpe"
    evening_route = f"/workouts/{evening.id}/rpe"
    assert owner.get(API + workout_list + "?limit=1").json()["missing_rpe_count"] == 2
    assert other.get(API + workout_list).status_code == 404
    assert owner.get(API + f"/workouts/{foreign.id}").status_code == 404
    assert other.get(API + route).status_code == 404
    assert owner.get(API + route).json() is None
    assert write(other, "PUT", route, {"score": 7}, other_csrf).status_code == 404
    assert write(coach, "PUT", route, {"score": 7}, coach_csrf).status_code == 403
    assert write(owner, "PUT", route, {"score": -1}, csrf).status_code == 422
    assert write(owner, "PUT", route, {"score": 11}, csrf).status_code == 422
    assert write(owner, "PUT", route, {"score": 3.5}, csrf).status_code == 422

    zero = write(owner, "PUT", route, {"score": 0}, csrf).json()
    assert zero["session_load"] == 0
    assert zero["created_at"] == zero["updated_at"]
    assert owner.get(API + workout_list).json()["missing_rpe_count"] == 1
    edited = write(owner, "PUT", route, {"score": 7, "comment": "Интервалы"}, csrf).json()
    assert edited["session_load"] == 420
    assert edited["created_at"] == zero["created_at"] and edited["updated_at"] > zero["updated_at"]
    assert write(owner, "PUT", evening_route, {"score": 10}, csrf).json()["session_load"] is None
    assert coach.get(API + route).json()["comment"] == "Интервалы"
    assert owner.get(API + workout_list).json()["missing_rpe_count"] == 0
    with Session(owner.app.state.engine) as db:
        assert db.scalar(select(func.count()).select_from(RpeEntry)) == 2
