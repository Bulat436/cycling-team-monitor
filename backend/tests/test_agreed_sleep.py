import socket
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import sleep_routes, wellness
from app.athlete_models import AthleteProfile, WellnessEntry
from app.cli import bootstrap_coach
from app.config import Settings
from app.database import make_engine
from app.garmin_models import GarminSleepObservation
from app.garmin_personal import initialize, personal_settings
from app.garmin_sync import GarminPersonalStore
from app.models import User, now
from app.security import passwords
from app.sleep import resolve_sleep
from app.sleep_models import SleepDay
from app.training_intelligence import routes as training_routes
from app.training_intelligence.schemas import Rules
from app.training_intelligence.service import analysis_window, calculate_measurements
from app.training_intelligence.warnings import athlete_warnings
from conftest import COACH_EMAIL, ORIGIN
from seed_training_pilot import AS_OF as PROCESS_DATE
from test_athletes import API, DIARY, edit_profile, team, write
from test_garmin_personal import DAY as GARMIN_DAY, FAKE_KEY, Clock, night, provider
from test_training_pilot import pilot_windows_process
from test_training_warnings import A as FATIGUE, AS_OF, START, synthetic_rows

TODAY = date(2026, 11, 22)
DAY = TODAY - timedelta(days=1)


@pytest.fixture(autouse=True)
def stable_date(monkeypatch):
    timestamp = datetime(2026, 11, 22, 12, tzinfo=UTC).timestamp()
    for module in (sleep_routes, wellness, training_routes):
        monkeypatch.setattr(module, "time", lambda: timestamp)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def sleep_path(athlete_id, day=DAY):
    return f"/athletes/{athlete_id}/sleep/{day}"


def patch(owner, csrf, athlete_id, day=DAY, version=0, **changes):
    return write(owner, "PATCH", sleep_path(athlete_id, day) + "/manual", {"version": version, **changes}, csrf)


def point(client, athlete_id, day=DAY):
    response = client.get(API + f"/training-intelligence/athletes/{athlete_id}/measurements")
    data = ok(response)
    return next(row for row in data["recovery"] if row["date"] == str(day)), data


def test_manual_sleep_without_an_account_or_questionnaire(team):
    (coach, _), (owner, athlete_id, csrf), _ = team
    path = sleep_path(athlete_id)
    empty = ok(owner.get(API + path))
    assert empty["version"] == 0 and empty["updated_at"] is None and empty["source"] is None
    partial = ok(patch(owner, csrf, athlete_id, start_time="23:15", sleep_quality=5, recovery=4))
    assert partial["start_time"] == "23:15" and partial["end_time"] is None
    assert partial["duration_minutes"] is None and partial["sleep_quality"] == 5
    assert not partial["has_diary"]
    overnight = ok(patch(owner, csrf, athlete_id, version=partial["version"], end_time="07:00"))
    assert overnight["duration_minutes"] == 465 and overnight["source"] == "manual"
    assert overnight["start_offset_minutes"] == overnight["end_offset_minutes"] == 180
    edited = ok(patch(owner, csrf, athlete_id, version=overnight["version"], duration_minutes=420))
    assert edited["duration_minutes"] == 420 and edited["calculated_duration_minutes"] == 465
    assert edited["entered_duration_minutes"] == 420 and edited["manually_corrected"]
    assert ok(coach.get(API + path)) == edited
    history = ok(owner.get(API + f"/athletes/{athlete_id}/sleep", params={"limit": 1}))
    assert history["entries"] == [edited] and not history["has_more"]
    assert ok(owner.get(API + f"/athletes/{athlete_id}/wellness/{DAY}")) is None
    day, analytics = point(coach, athlete_id)
    assert day["metrics"]["sleep_minutes"]["value"] == 420
    assert day["metrics"]["sleep_quality"]["value"] == 5 and day["metrics"]["recovery"]["value"] == 4
    assert not day["has_entry"] and str(DAY) in analytics["missing_diary_dates"]
    reverted = ok(patch(owner, csrf, athlete_id, version=edited["version"], duration_minutes=None))
    assert reverted["duration_minutes"] == 465 and not reverted["manually_corrected"]
    assert patch(owner, csrf, athlete_id, TODAY + timedelta(days=1), duration_minutes=400).status_code == 422


def test_legacy_conflict_selection_and_old_diary_requests(team):
    (coach, _), (owner, athlete_id, csrf), _ = team
    with Session(owner.app.state.engine) as db, db.begin():
        db.add(WellnessEntry(athlete_id=athlete_id, entry_date=DAY, timezone="Europe/Moscow", sleep_minutes=450, **DIARY))
    path = sleep_path(athlete_id)
    diary_path = f"/athletes/{athlete_id}/wellness/{DAY}"
    legacy = ok(owner.get(API + path))
    assert legacy["source"] == "legacy" and legacy["candidates"] == {"legacy": 450}
    manual = ok(patch(owner, csrf, athlete_id, start_time="22:30", end_time="07:00", duration_minutes=420))
    assert manual["conflict"] and manual["candidates"] == {"manual": 420, "legacy": 450}
    assert manual["source"] == "manual" and manual["manually_corrected"]
    assert ok(owner.get(API + diary_path))["sleep_minutes"] == 420

    stale = write(owner, "PUT", path + "/selection", {"source": "legacy", "version": 0}, csrf)
    assert stale.status_code == 409 and stale.json()["detail"] == "sleep_version_conflict"
    selected = ok(write(owner, "PUT", path + "/selection", {"source": "legacy", "version": manual["version"]}, csrf))
    assert selected["source"] == selected["selected_source"] == "legacy"
    assert selected["duration_minutes"] == 450 and selected["conflict"] and not selected["manually_corrected"]
    assert ok(write(owner, "PUT", diary_path, DIARY | {"sleep_minutes": 450}, csrf))["sleep_minutes"] == 450
    assert ok(owner.get(API + path))["version"] == selected["version"]
    assert point(coach, athlete_id)[0]["metrics"]["sleep_minutes"]["value"] == 450

    assert ok(write(owner, "PUT", diary_path, DIARY | {"sleep_minutes": None}, csrf))["sleep_minutes"] is None
    cleared = ok(owner.get(API + path))
    assert cleared["duration_minutes"] is None and cleared["source"] is None
    assert cleared["start_time"] == "22:30" and cleared["candidates"]["legacy"] == 450
    assert ok(write(owner, "PUT", diary_path, DIARY, csrf))["sleep_minutes"] is None
    restored = ok(write(owner, "PUT", path + "/selection", {"source": "legacy", "version": cleared["version"]}, csrf))
    assert restored["duration_minutes"] == 450
    assert ok(write(owner, "PUT", diary_path, DIARY | {"sleep_minutes": 0}, csrf))["sleep_minutes"] == 0
    assert ok(owner.get(API + path))["candidates"] == {"manual": 0, "legacy": 450}
    assert point(coach, athlete_id)[0]["metrics"]["sleep_minutes"]["value"] == 0
    with Session(owner.app.state.engine) as db:
        assert len(db.scalars(select(SleepDay).where(SleepDay.athlete_id == athlete_id)).all()) == 1


def test_dst_and_profile_timezone_change(team):
    (coach, coach_csrf), (owner, athlete_id, csrf), _ = team
    assert edit_profile(coach, coach_csrf, athlete_id, timezone="Europe/Berlin").status_code == 204
    spring, fall = date(2026, 3, 29), date(2026, 10, 25)
    nonexistent = patch(owner, csrf, athlete_id, spring, end_time="02:30")
    assert nonexistent.status_code == 422 and nonexistent.json()["detail"]["code"] == "sleep_time_nonexistent"
    assert ok(patch(owner, csrf, athlete_id, spring, start_time="23:30", end_time="07:30"))["duration_minutes"] == 420
    ambiguous = patch(owner, csrf, athlete_id, fall, start_time="23:30", end_time="02:30")
    assert ambiguous.status_code == 422
    assert ambiguous.json()["detail"] == {"code": "sleep_time_ambiguous", "field": "end_time", "offset_minutes": [60, 120]}
    late = ok(patch(owner, csrf, athlete_id, fall, start_time="23:30", end_time="02:30", end_offset_minutes=60))
    assert late["duration_minutes"] == 240 and late["start_offset_minutes"] == 120
    assert edit_profile(coach, coach_csrf, athlete_id, timezone="America/New_York").status_code == 204
    edited = ok(patch(owner, csrf, athlete_id, fall, version=late["version"], end_time="03:30"))
    assert edited["timezone"] == "Europe/Berlin" and edited["duration_minutes"] == 300
    newest = ok(patch(owner, csrf, athlete_id, end_time="07:00"))
    assert newest["timezone"] == "America/New_York" and newest["end_offset_minutes"] == -300
    assert newest["duration_minutes"] is None


def test_moving_manual_sleep_preserves_legacy_and_rejects_occupied_dates(team):
    (coach, _), (owner, athlete_id, csrf), _ = team
    previous, target = DAY - timedelta(days=2), DAY - timedelta(days=1)
    with Session(owner.app.state.engine) as db, db.begin():
        db.add(WellnessEntry(athlete_id=athlete_id, entry_date=previous, timezone="Europe/Moscow", sleep_minutes=450, **DIARY))
    manual = ok(patch(owner, csrf, athlete_id, previous, start_time="23:30", end_time="07:30"))
    occupied = ok(patch(owner, csrf, athlete_id, DAY, duration_minutes=400))
    move_path = sleep_path(athlete_id, previous) + "/move"
    rejected = write(owner, "POST", move_path, {"date": str(DAY), "version": manual["version"]}, csrf)
    assert rejected.status_code == 409 and rejected.json()["detail"] == "sleep_date_occupied"
    assert ok(owner.get(API + sleep_path(athlete_id))) == occupied
    assert ok(owner.get(API + sleep_path(athlete_id, previous))) == manual
    moved = ok(write(owner, "POST", move_path, {"date": str(target), "version": manual["version"]}, csrf))
    assert moved["date"] == str(target) and moved["duration_minutes"] == 480 and not moved["has_diary"]
    original = ok(owner.get(API + sleep_path(athlete_id, previous)))
    assert original["source"] == "legacy" and original["duration_minutes"] == 450 and original["has_diary"]
    assert original["start_time"] is None and not original["conflict"]
    assert point(coach, athlete_id, previous)[0]["metrics"]["sleep_minutes"]["value"] == 450
    assert point(coach, athlete_id, target)[0]["metrics"]["sleep_minutes"]["value"] == 480
    assert patch(owner, csrf, athlete_id, previous, version=manual["version"], duration_minutes=495).status_code == 409


def test_analytics_and_questionnaire_coverage_recalculate_independently(team):
    (coach, _), (owner, athlete_id, csrf), _ = team
    for offset, minutes in [(29, 900), (28, 420), *enumerate((430, 440, 450, 460, 470, 480), start=1)]:
        ok(patch(owner, csrf, athlete_id, DAY - timedelta(days=offset), duration_minutes=minutes))
    target = ok(patch(owner, csrf, athlete_id, duration_minutes=380, sleep_quality=2, recovery=3))
    measured, before = point(coach, athlete_id)
    assert measured["metrics"]["sleep_minutes"] == {
        "value": 380, "baseline": 450.0, "observations": 7, "delta": -70.0, "status": "sufficient",
        "deteriorated": True, "within_baseline": False,
    }
    assert measured["metrics"]["general_fatigue"]["value"] is None
    oldest = DAY - timedelta(days=28)
    ok(patch(owner, csrf, athlete_id, oldest, version=1, duration_minutes=540))
    ok(patch(owner, csrf, athlete_id, version=target["version"], duration_minutes=455))
    edited, after = point(coach, athlete_id)
    assert edited["metrics"]["sleep_minutes"]["delta"] == -5
    assert before["load"] == after["load"] and before["performance"] == after["performance"]
    assert str(DAY) in after["missing_diary_dates"]
    diary = ok(write(owner, "PUT", f"/athletes/{athlete_id}/wellness/{DAY}", DIARY, csrf))
    assert diary["sleep_minutes"] == 455 and diary["recovery"] == 3
    completed, result = point(coach, athlete_id)
    assert completed["has_entry"] and str(DAY) not in result["missing_diary_dates"]
    assert completed["metrics"]["sleep_quality"]["value"] == DIARY["sleep_quality"]


def test_sleep_routes_enforce_owner_origin_csrf_and_allowed_sources(team):
    (coach, coach_csrf), (owner, athlete_id, csrf), (other, _, other_csrf) = team
    path = sleep_path(athlete_id)
    edit = {"version": 0, "duration_minutes": 420}
    assert other.get(API + path).status_code == 404
    assert write(other, "PATCH", path + "/manual", edit, other_csrf).status_code == 404
    assert write(coach, "PATCH", path + "/manual", edit, coach_csrf).status_code == 403
    assert write(owner, "PATCH", path + "/manual", edit).status_code == 403
    assert owner.patch(API + path + "/manual", json=edit, headers={"X-CSRF-Token": csrf}).status_code == 403
    with TestClient(owner.app, base_url=ORIGIN) as guest:
        assert guest.get(API + path).status_code == 401
    for source in ("garmin", "automatic"):
        assert write(owner, "PUT", path + "/selection", {"version": 0, "source": source}, csrf).status_code == 422
    assert patch(owner, csrf, athlete_id, source="garmin").status_code == 422
    assert ok(owner.get(API + path))["version"] == 0


def test_agreed_duration_is_used_in_g3_warning_evidence():
    rules = Rules()
    profile = AthleteProfile(athlete_id=1, timezone="UTC")
    rows, diary = synthetic_rows(1, latest_tss=120, bad=(39, 40, 41))
    window = analysis_window(profile, 45, AS_OF, rules)
    day = START + timedelta(days=41)
    for source, expected in (("manual", 370), ("legacy", 400)):
        sleep = {day: SleepDay(manual_minutes=370, legacy_minutes=400, selected_source=source)}
        measures = calculate_measurements(profile, window, rows, diary, rules, [], sleep=sleep, for_warnings=True)
        warning = next(item for item in athlete_warnings(measures).warnings if item.type == FATIGUE)
        fact = next(item for item in warning.facts if item.metric == "sleep_minutes" and item.date == day)
        assert (fact.value, fact.baseline, fact.delta) == (expected, 450, expected - 450)


def test_athlete_sleep_trend_reuses_metrics_and_exposes_the_configured_window(team):
    (coach, coach_csrf), (owner, athlete_id, csrf), _ = team
    settings = ok(coach.get(API + "/training-intelligence/settings"))
    ok(write(coach, "PUT", "/training-intelligence/settings", settings | {
        "warning_diary_window_days": 45, "recovery_window_days": 35,
    }, coach_csrf))
    old_day = TODAY - timedelta(days=20)
    ok(patch(owner, csrf, athlete_id, old_day, duration_minutes=0))
    route = API + "/training-intelligence/me"
    default = ok(owner.get(route))
    extended = ok(owner.get(route, params={"period": "28"}))
    assert extended["load"] == default["load"] and extended["recovery_window_days"] == 35
    assert len(extended["recovery"]) == 28 and len(default["recovery"]) == 8
    assert extended["recovery"][-1]["date"] == str(TODAY)
    points = {row["date"]: row for row in extended["recovery"]}
    assert points[str(old_day)]["metrics"]["sleep_minutes"]["value"] == 0
    assert points[str(old_day - timedelta(days=1))]["metrics"]["sleep_minutes"]["value"] is None
    assert coach.get(route, params={"period": "28"}).status_code == 403


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runtime check")
def test_manual_sleep_remains_in_history_and_both_roles_after_process_restart(migration, password):
    path, config = migration
    isolated = path.parent
    settings = Settings(_env_file=None, database_path=path, fit_path=isolated / "fit")
    config.attributes["settings"] = settings
    command.upgrade(config, "head")
    bootstrap_coach(settings, COACH_EMAIL, "Тестовый тренер", password)
    engine = make_engine(settings)
    owner_email = "g65-restart@example.com"
    try:
        with Session(engine) as db, db.begin():
            owner = User(email=owner_email, name="Тестовый спортсмен", role="athlete", password_hash=passwords.hash(password), activated_at=now())
            db.add(owner)
            db.flush()
            athlete_id = owner.id
            db.add(AthleteProfile(athlete_id=athlete_id, timezone="UTC"))
    finally:
        engine.dispose()

    dist = isolated / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "icons").mkdir()
    for name in ("index.html", "manifest.webmanifest", "sw.js"):
        (dist / name).touch()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    day = PROCESS_DATE.date() - timedelta(days=1)
    base = API + f"/athletes/{athlete_id}/sleep"
    night_path = f"{base}/{day}"
    own_path = API + "/training-intelligence/me?period=28"
    coach_path = API + f"/training-intelligence/athletes/{athlete_id}/measurements?period=28"

    def metric(data):
        row = next(row for row in data["recovery"] if row["date"] == str(day))
        assert not row["has_entry"]
        return row["metrics"]["sleep_minutes"]

    with httpx.Client(base_url=origin, trust_env=False) as athlete, httpx.Client(base_url=origin, trust_env=False) as coach:
        with pilot_windows_process(settings, isolated, port) as first_pid:
            assert ok(athlete.get(API + "/health")) == {"status": "ok"}
            athlete_login = ok(athlete.post(API + "/auth/login", headers={"Origin": origin}, json={"email": owner_email, "password": password}))
            coach_login = ok(coach.post(API + "/auth/login", headers={"Origin": origin}, json={"email": COACH_EMAIL, "password": password}))
            headers = {"Origin": origin, "X-CSRF-Token": athlete_login["csrf_token"]}
            for days in range(1, 8):
                prior = day - timedelta(days=days)
                ok(athlete.patch(f"{base}/{prior}/manual", headers=headers, json={"version": 0, "duration_minutes": 450}))
            created = ok(athlete.patch(night_path + "/manual", headers=headers, json={
                "version": 0, "start_time": "23:15", "end_time": "07:00", "sleep_quality": 4, "recovery": 3,
            }))
            assert created["duration_minutes"] == 465
            assert ok(athlete.get(base))["entries"][0] == created
            before = metric(ok(athlete.get(own_path)))
            assert (before["value"], before["baseline"], before["delta"]) == (465, 450, 15)
            saved = ok(athlete.patch(night_path + "/manual", headers=headers, json={"version": created["version"], "duration_minutes": 400}))
            assert saved["duration_minutes"] == 400 and saved["calculated_duration_minutes"] == 465
            assert ok(coach.get(night_path)) == saved
            expected = metric(ok(athlete.get(own_path)))
            assert metric(ok(coach.get(coach_path))) == expected
            assert (expected["value"], expected["baseline"], expected["delta"]) == (400, 450, -50)
            denied = coach.patch(night_path + "/manual", headers={"Origin": origin, "X-CSRF-Token": coach_login["csrf_token"]}, json={"version": saved["version"], "duration_minutes": 410})
            assert denied.status_code == 403 and denied.json()["detail"] == "role_denied"

        with pilot_windows_process(settings, isolated, port) as second_pid:
            assert second_pid != first_pid
            assert ok(athlete.get(API + "/health")) == {"status": "ok"}
            assert ok(athlete.get(base))["entries"][0] == saved
            assert ok(coach.get(night_path)) == saved
            assert metric(ok(athlete.get(own_path))) == metric(ok(coach.get(coach_path))) == expected


def test_automatic_candidate_changes_never_replace_a_manual_value_or_choice():
    for imported in (430, 470, 470):
        result = resolve_sleep(410, 415, automatic=imported, selected="manual")
        assert result.minutes == 410 and result.source == "manual" and result.conflict
        assert result.candidates == {"manual": 410, "legacy": 415, "automatic": imported}
    assert resolve_sleep(0, None, automatic=440).minutes == 0
    assert resolve_sleep(None, None, automatic=440).minutes == 440
    assert resolve_sleep(None, 415, automatic=440).minutes == 415
    assert resolve_sleep(410, None, automatic=440, selected="automatic").minutes == 440
    assert resolve_sleep(None, None).minutes is None


def test_personal_resync_is_isolated_from_team_view_and_analytics(team, tmp_path):
    (coach, _), (owner, athlete_id, csrf), _ = team
    patch_day = GARMIN_DAY + timedelta(days=1)
    own = ok(patch(owner, csrf, athlete_id, patch_day, duration_minutes=420))
    own_path = sleep_path(athlete_id, patch_day)
    ok(write(owner, "PUT", own_path + "/selection", {"version": own["version"], "source": "manual"}, csrf))
    before = ok(coach.get(API + own_path))
    settings = personal_settings(tmp_path)
    initialize(settings, "Europe/Moscow")
    clock = Clock()
    engine = make_engine(settings)
    try:
        store = GarminPersonalStore(engine, clock=clock.now, wait=clock.advance)
        store.configure(90, 1)
        for seconds, expected in [(28_800, 1), (27_900, 1), (27_900, 0)]:
            result = store.sync(GARMIN_DAY, GARMIN_DAY, FAKE_KEY, transport=provider(clock, [night(sleepTimeSeconds=seconds)]))
            assert result.inserted == expected
        with Session(engine) as db:
            assert db.scalars(select(GarminSleepObservation.sleep_seconds).order_by(GarminSleepObservation.id)).all() == [28_800, 27_900]
            assert db.scalars(select(SleepDay)).all() == []
    finally:
        engine.dispose()
    assert ok(coach.get(API + own_path)) == before
    assert point(coach, athlete_id, patch_day)[0]["metrics"]["sleep_minutes"]["value"] == 420
    for viewer in (owner, coach):
        for route in (f"/athletes/{athlete_id}/garmin", f"/athletes/{athlete_id}/garmin/sleep"):
            response = viewer.get(API + route)
            assert response.status_code == 404 and response.headers["Cache-Control"] == "no-store"
            assert FAKE_KEY not in response.text
    with Session(owner.app.state.engine) as db:
        assert db.scalars(select(GarminSleepObservation)).all() == []


def test_sleep_migration_backfills_values_and_rolls_back_without_touching_diary(migration):
    path, config = migration
    command.upgrade(config, "0008_garmin_personal")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO users (id,email,name,role,active,created_at,updated_at) VALUES (12,'g63@example.test','Test','athlete',1,1,1)")
        db.execute("INSERT INTO athlete_profiles (athlete_id,timezone,created_at,updated_at) VALUES (12,'Asia/Tokyo',1,1)")
        db.executemany(
            "INSERT INTO wellness_entries (athlete_id,entry_date,timezone,sleep_quality,general_fatigue,leg_fatigue,"
            "motivation,mood,muscle_soreness,illness_symptoms,sleep_minutes,created_at,updated_at) "
            "VALUES (12,?,'Europe/Berlin',3,2,4,4,3,2,0,?,1,2)",
            [("2026-11-18", 450), ("2026-11-19", None), ("2026-11-20", 0)],
        )
        before = db.execute("SELECT * FROM wellness_entries ORDER BY entry_date").fetchall()
    for migrate, target in ((command.upgrade, "head"), (command.downgrade, "0008_garmin_personal"), (command.upgrade, "head")):
        migrate(config, target)
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("SELECT * FROM wellness_entries ORDER BY entry_date").fetchall() == before
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
            if target == "head":
                assert db.execute(
                    "SELECT sleep_date,timezone,legacy_minutes,updated_at FROM sleep_days ORDER BY sleep_date"
                ).fetchall() == [("2026-11-18", "Europe/Berlin", 450, 2), ("2026-11-20", "Europe/Berlin", 0, 2)]
            else:
                assert db.execute("SELECT name FROM sqlite_master WHERE name='sleep_days'").fetchone() is None
