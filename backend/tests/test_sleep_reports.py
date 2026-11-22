import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.sleep_models import PrivateSleepReportNight
from app.sleep_report_schemas import MAX_SLEEP_REPORT_BYTES
from conftest import ORIGIN
from test_athletes import API, sign_in, team, write


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def test_documented_report_preview_confirm_and_retry(team, password):
    _, (owner, athlete_id, csrf), _ = team
    path = f"/athletes/{athlete_id}/sleep-reports"
    example = Path(__file__).resolve().parents[2] / "docs" / "sleep-report-v1.example.json"
    report = json.loads(example.read_text(encoding="utf-8"))
    preview = ok(write(owner, "POST", path + "/preview", report, csrf))
    assert preview == report
    assert ok(owner.get(API + path)) == {"entries": [], "has_more": False}

    zero = {"sleep_date": "2026-11-21", "duration_minutes": 0}
    report["sleep"].append(zero)
    assert ok(write(owner, "PUT", path, report, csrf)) == {"saved": 2, "unchanged": 0}
    assert ok(write(owner, "PUT", path, report, csrf)) == {"saved": 0, "unchanged": 2}
    page = ok(owner.get(API + path + "?limit=1"))
    assert page == {"entries": [report["sleep"][0]], "has_more": True}
    response = owner.get(API + path + "?offset=1&limit=1")
    assert response.headers["Cache-Control"] == "no-store"
    assert ok(response)["entries"][0]["duration_minutes"] == 0
    assert ok(response)["entries"][0]["sleep_score"] is None

    # The offset change is valid even though the clock skips an hour.
    spring = {"sleep_date": "2026-03-29", "start_at": "2026-03-28T22:30:00+01:00",
              "end_at": "2026-03-29T07:00:00+02:00", "duration_minutes": 425}
    revised = {"sleep_date": "2026-11-22", "duration_minutes": 440}
    assert ok(write(owner, "PUT", path, {"schema_version": 1, "sleep": [spring, revised]}, csrf))["saved"] == 2
    with TestClient(owner.app, base_url=ORIGIN) as later:
        sign_in(later, "one@example.com", password)
        entries = ok(later.get(API + path))["entries"]
        assert [(night["sleep_date"], night["duration_minutes"]) for night in entries] == [
            ("2026-11-22", 440), ("2026-11-21", 0), ("2026-03-29", 425),
        ]
        assert entries[0]["sleep_score"] is None
        assert entries[-1]["end_at"] == spring["end_at"]


def test_report_is_visible_only_to_its_owner_and_does_not_change_analytics(team):
    (coach, coach_csrf), (owner, athlete_id, csrf), (other, other_id, other_csrf) = team
    root = f"/athletes/{athlete_id}"
    path = root + "/sleep-reports"
    day = (datetime.now(UTC).date() - timedelta(days=2)).isoformat()
    manual_path = root + f"/sleep/{day}"
    analytics_path = f"/training-intelligence/athletes/{athlete_id}/measurements"
    report = {"schema_version": 1, "sleep": [{"sleep_date": day, "duration_minutes": 471, "sleep_score": 99}]}
    manual = ok(write(owner, "PATCH", manual_path + "/manual", {"version": 0, "duration_minutes": 420}, csrf))
    before = ok(coach.get(API + analytics_path))
    assert ok(write(owner, "PUT", path, report, csrf)) == {"saved": 1, "unchanged": 0}
    assert ok(coach.get(API + manual_path)) == manual
    assert ok(coach.get(API + analytics_path)) == before
    assert ok(other.get(API + f"/athletes/{other_id}/sleep-reports"))["entries"] == []

    with TestClient(owner.app, base_url=ORIGIN) as anonymous:
        for viewer, token, status in ((coach, coach_csrf, 403), (other, other_csrf, 404), (anonymous, None, 401)):
            assert viewer.get(API + path).status_code == status
            assert write(viewer, "POST", path + "/preview", report, token).status_code == status
            assert write(viewer, "PUT", path, report, token).status_code == status
    assert write(owner, "PUT", path, report).json()["detail"] == "csrf_invalid"
    assert owner.put(API + path, json=report, headers={"X-CSRF-Token": csrf}).json()["detail"] == "origin_denied"


def test_invalid_reports_are_atomic_and_do_not_echo_arbitrary_content(team, caplog):
    _, (owner, athlete_id, csrf), _ = team
    path = f"/athletes/{athlete_id}/sleep-reports"
    normal = {"sleep_date": "2026-11-21", "duration_minutes": 450}
    sensitive = "synthetic-key-must-not-be-echoed"
    cases = [
        ({"schema_version": True, "sleep": [normal]}, "sleep_report_invalid"),
        ({"schema_version": 1, "sleep": [normal, normal]}, "sleep_report_duplicate_date"),
        ({"schema_version": 1, "sleep": [normal] * 367}, "sleep_report_invalid"),
        ({"schema_version": 1, "sleep": [normal], "token": sensitive}, "sleep_report_invalid"),
    ]
    bad_nights = [
        {"start_at": "2026-11-22T00:00:00"}, {"end_at": "2026-11-23T07:00:00Z"},
        {"start_at": "2026-11-22T09:00:00+03:00", "end_at": "2026-11-22T07:00:00+03:00"},
        {"duration_minutes": "430"}, {"duration_minutes": 1441}, {"sleep_score": True},
        {"avg_heart_rate_bpm": 0}, {"duration_minutes": None}, {sensitive: sensitive},
    ]
    cases.extend(({"schema_version": 1, "sleep": [normal, {"sleep_date": "2026-11-22", **fields}]},
                  {"code": "sleep_report_night_invalid", "field": "2"}) for fields in bad_nights)
    for report, detail in cases:
        response = write(owner, "PUT", path, report, csrf)
        assert (response.status_code, response.json()["detail"]) == (422, detail)
        assert sensitive not in response.text

    headers = {"Content-Type": "application/json", "Origin": ORIGIN, "X-CSRF-Token": csrf}
    for blob in (b"\xff", b"{", b'{"schema_version":1,"schema_version":1,"sleep":[]}'):
        assert owner.put(API + path, content=blob, headers=headers).json()["detail"] == "sleep_report_invalid"
    large = owner.put(API + path, content=b" " * (MAX_SLEEP_REPORT_BYTES + 1), headers=headers)
    assert large.status_code == 413
    assert owner.put(API + path, content=b"{}", headers=headers | {"Content-Type": "text/plain"}).status_code == 415
    assert sensitive not in caplog.text
    with Session(owner.app.state.engine) as db:
        assert db.scalars(select(PrivateSleepReportNight)).all() == []


def test_private_report_migration_rolls_back_only_uploaded_data(migration):
    path, config = migration
    command.upgrade(config, "0009_agreed_sleep")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO users (id,email,name,role,active,created_at,updated_at) VALUES (12,'a@example.com','A','athlete',1,1,1)")
        db.execute("INSERT INTO athlete_profiles (athlete_id,timezone,created_at,updated_at) VALUES (12,'Asia/Tokyo',1,1)")
        db.execute("INSERT INTO sleep_days (athlete_id,sleep_date,timezone,manual_minutes,version,updated_at) "
                   "VALUES (12,'2026-11-22','Asia/Tokyo',420,1,1)")
        before = db.execute("SELECT * FROM sleep_days").fetchall()
    command.upgrade(config, "head")
    command.check(config)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO private_sleep_report_nights VALUES (12,'2026-11-22',?,1)", ('{"duration_minutes":470}',))
    command.downgrade(config, "0009_agreed_sleep")
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT * FROM sleep_days").fetchall() == before
        assert db.execute("SELECT name FROM sqlite_master WHERE name='private_sleep_report_nights'").fetchone() is None
    command.upgrade(config, "head")
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT * FROM sleep_days").fetchall() == before
        assert db.execute("SELECT * FROM private_sleep_report_nights").fetchall() == []
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
