import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from hashlib import sha256
from urllib.parse import quote

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fit_parser import FitProblem
from app.workout_models import WorkoutImport, WorkoutFile, WorkoutSource
from conftest import ORIGIN
from fit_fixtures import START, cycling_fit, magene_wrapper
from test_athletes import API, edit_profile, team, write


def upload(client, payload, csrf, filename="ride.fit", origin=ORIGIN):
    headers = {"Origin": origin, "Content-Type": "application/octet-stream", "X-FIT-Filename": quote(filename)}
    if csrf:
        headers["X-CSRF-Token"] = csrf
    return client.post(API + "/workouts/import", headers=headers, content=payload)


def update_ftp(coach, csrf, athlete_id, date, watts):
    return write(coach, "PUT", f"/athletes/{athlete_id}/ftp/{date}", {"watts": watts}, csrf)


def test_fit_import_deduplicates_links_rpe_and_preserves_ftp_snapshot(team):
    (coach, coach_csrf), (owner, athlete_id, csrf), (other, other_id, other_csrf) = team
    assert edit_profile(coach, coach_csrf, athlete_id, timezone="Europe/Moscow").status_code == 204
    for date, watts in (("2026-01-01", 300), ("2026-01-02", 250), ("2026-01-03", 350)):
        assert update_ftp(coach, coach_csrf, athlete_id, date, watts).status_code == 200
    zones = {"zones": [{"name": "Первая", "minimum": 0, "maximum": 300}]}
    assert write(coach, "PUT", f"/athletes/{athlete_id}/zones/power", zones, coach_csrf).status_code == 200

    fit = cycling_fit()
    assert upload(owner, fit, None).status_code == 403
    assert upload(owner, fit, csrf, origin="https://other.example.com").status_code == 403
    assert upload(coach, fit, coach_csrf).status_code == 403
    response = upload(owner, fit, csrf, filename="..\\..\\Тренировка.FIT")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "imported"
    workout_id = body["workout_id"]
    path = f"/workouts/{workout_id}"
    detail = owner.get(API + path).json()
    assert detail["athlete_id"] == athlete_id and detail["started_at"] == START
    assert detail["summary"]["distance_m"] == 600
    assert detail["np"] == 200 and detail["metrics"]["result"]["if_value"] == .8
    assert detail["metrics"]["snapshot"]["local_date"] == "2026-01-02"
    assert detail["metrics"]["snapshot"]["ftp_watts"] == 250
    assert detail["metrics"]["result"]["power_zones"]["zones"][0]["seconds"] == 120
    assert owner.get(API + path + "/fit").content == fit
    for endpoint in (path, path + "/fit", path + "/samples", path + "/metrics", path + "/rpe"):
        assert other.get(API + endpoint).status_code == 404
    assert write(other, "PUT", path + "/rpe", {"score": 7}, other_csrf).status_code == 404
    assert write(owner, "POST", path + "/reprocess", {}, csrf).status_code == 403
    assert coach.get(API + path + "/metrics").status_code == 200

    result = upload(owner, fit, csrf, filename="renamed.fit").json()
    assert result["status"] == "duplicate" and result["workout_id"] == workout_id
    service = owner.app.state.workout_import
    path_on_disk = service.original_path(athlete_id, sha256(fit).hexdigest())
    assert path_on_disk.name == sha256(fit).hexdigest() + ".fit"
    assert path_on_disk.read_bytes() == fit
    assert owner.get("/fit/" + path_on_disk.name).status_code == 404
    source = service.import_fit(athlete_id, magene_wrapper(fit), filename="device.fit", source="onelapfit", external_id="race-1")
    assert source.status == "duplicate" and source.workout_id == workout_id
    assert len(owner.get(API + path).json()["sources"]) == 2
    sample = owner.get(API + path + "/samples?offset=1&limit=2&include_fields=true").json()
    assert sample["total"] == 120 and sample["samples"][0]["timestamp"] == START + 1
    assert any(field["field"] == 200 for field in sample["samples"][0]["fields"])
    assert "fields" not in owner.get(API + path + "/samples?limit=1").json()["samples"][0]
    assert upload(other, fit, other_csrf).json()["workout_id"] != workout_id

    rpe = write(owner, "PUT", path + "/rpe", {"score": 7}, csrf).json()
    assert rpe["session_load"] == 14
    assert update_ftp(coach, coach_csrf, athlete_id, "2026-01-02", 200).status_code == 200
    assert write(coach, "POST", path + "/reprocess", {}, coach_csrf).status_code == 204
    assert owner.get(API + path).json()["metrics"]["snapshot"]["ftp_watts"] == 250
    assert write(coach, "POST", path + "/reprocess", {"refresh_parameters": True}, coach_csrf).status_code == 204
    after = owner.get(API + path).json()
    assert after["metrics"]["snapshot"]["ftp_watts"] == 200
    assert after["metrics"]["result"]["if_value"] == 1
    assert after["rpe"] == rpe


def test_upload_errors_are_bounded_and_logged(team, monkeypatch):
    (_, _), (owner, athlete_id, csrf), (_, _, _) = team
    app = owner.app
    fit = cycling_fit()
    cases = [
        (fit, "ride.zip", 422, "fit_extension"),
        (b"", "ride.fit", 422, "fit_empty"),
        (b"Not Fit", "ride.fit", 422, "fit_invalid_signature"),
        (fit[:-3], "ride.fit", 422, "fit_invalid_structure"),
        (fit[:-1] + bytes([fit[-1] ^ 1]), "ride.fit", 422, "fit_bad_crc"),
        (cycling_fit(file_type=6), "course.fit", 422, "fit_not_activity"),
    ]
    for content, filename, status, code in cases:
        response = upload(owner, content, csrf, filename)
        assert (response.status_code, response.json()["detail"]) == (status, code)
    monkeypatch.setattr(app.state.workout_import, "max_bytes", 1024)
    for data in (fit, iter([b"a" * 500, b"b" * 600])):
        response = upload(owner, data, csrf)
        assert response.status_code == 413 and response.json()["detail"] == "fit_too_large"
    assert owner.get(f"{API}/athletes/{athlete_id}/workouts").json()["workouts"] == []
    with Session(app.state.engine) as db:
        attempts = db.scalars(select(WorkoutImport).order_by(WorkoutImport.id)).all()
        assert [attempt.error_code for attempt in attempts] == [case[3] for case in cases] + ["fit_too_large"] * 2
        assert not list(db.scalars(select(WorkoutFile)))


def test_simultaneous_uploads_are_idempotent_and_samples_page_across_blocks(team):
    (_, _), (owner, athlete_id, csrf), (_, _, _) = team
    payload = cycling_fit(elapsed=600)

    def send(_):
        with TestClient(owner.app, base_url=ORIGIN, cookies=owner.cookies) as browser:
            response = upload(browser, payload, csrf)
            assert response.status_code == 200, response.text
            return response.json()

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(send, range(3)))
    assert sorted(row["status"] for row in results) == ["duplicate", "duplicate", "imported"]
    ids = {row["workout_id"] for row in results}
    assert len(ids) == 1
    workout_id = ids.pop()
    samples = owner.get(f"{API}/workouts/{workout_id}/samples?offset=510&limit=5").json()
    assert [row["timestamp"] for row in samples["samples"]] == list(range(START + 510, START + 515))
    assert samples["total"] == 600
    assert len(owner.get(f"{API}/athletes/{athlete_id}/workouts").json()["workouts"]) == 1


def test_provider_updates_stable_workout_and_retains_all_originals(team):
    (_, _), (owner, athlete_id, csrf), (_, _, _) = team
    service = owner.app.state.workout_import
    first, changed, similar = cycling_fit(), cycling_fit(power=225), cycling_fit(power=226)
    config = {"filename": "remote.fit", "source": "onelapfit", "external_id": "some-key"}
    one = service.import_fit(athlete_id, first, **config)
    path = f"/workouts/{one.workout_id}"
    assert write(owner, "PUT", path + "/rpe", {"score": 7}, csrf).status_code == 200
    two = service.import_fit(athlete_id, changed, **config)
    assert two.status == "updated" and two.workout_id == one.workout_id
    assert owner.get(API + path + "/fit").content == changed
    assert owner.get(API + path).json()["rpe"]["score"] == 7
    assert owner.get(API + path).json()["np"] == 225
    assert upload(owner, first, csrf).json()["status"] == "duplicate"
    assert owner.get(API + path + "/fit").content == changed
    three = upload(owner, similar, csrf).json()
    assert three["potential_duplicates"] == [one.workout_id]
    with pytest.raises(FitProblem, match="fit_source_conflict"):
        service.import_fit(athlete_id, similar, **config)
    with Session(owner.app.state.engine) as db:
        originals = db.scalars(select(WorkoutFile).where(WorkoutFile.workout_id == one.workout_id)).all()
        assert len(originals) == 2
        provider = db.scalar(select(WorkoutSource).where(WorkoutSource.source == "onelapfit"))
        assert provider.file_sha256 == sha256(changed).hexdigest()


def test_storage_failure_rolls_back_and_repeat_repairs_original(team, monkeypatch):
    (coach, coach_csrf), (owner, athlete_id, csrf), (_, _, _) = team
    service = owner.app.state.workout_import
    payload = cycling_fit()

    def fail(*args):
        raise OSError("offline storage")

    with monkeypatch.context() as patch:
        patch.setattr(service, "_save_original", fail)
        failure = upload(owner, payload, csrf)
        assert failure.status_code == 503 and failure.json()["detail"] == "fit_storage_error"
    assert owner.get(f"{API}/athletes/{athlete_id}/workouts").json()["workouts"] == []

    result = upload(owner, payload, csrf).json()
    route = f"/workouts/{result['workout_id']}"
    original = service.original_path(athlete_id, sha256(payload).hexdigest())
    original.write_bytes(b"damaged on disk")
    replay = write(coach, "POST", route + "/reprocess", {}, coach_csrf)
    assert replay.status_code == 409 and replay.json()["detail"] == "fit_original_changed"
    assert upload(owner, payload, csrf).json()["status"] == "duplicate"
    assert owner.get(API + route + "/fit").content == payload


def test_migration_leaves_legacy_workout_and_rpe_intact(migration):
    path, config = migration
    command.upgrade(config, "0002_athletes")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO users (id,email,name,role,active,created_at,updated_at) VALUES (1,'a@example.com','A','athlete',1,1,1)")
        db.execute("INSERT INTO athlete_profiles(athlete_id, timezone, created_at, updated_at) VALUES(1,'Etc/UTC',1,1)")
        db.execute("INSERT INTO workouts(id, athlete_id, started_at, timer_seconds, created_at, updated_at) VALUES(1,1,100,120,1,1)")
        db.execute("INSERT INTO rpe_entries(workout_id,score,created_at,updated_at) VALUES(1,7,1,1)")
    command.upgrade(config, "head")
    command.check(config)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT file_sha256,summary FROM workouts WHERE id=1").fetchone() == (None, None)
        assert db.execute("SELECT score FROM rpe_entries WHERE workout_id=1").fetchone() == (7,)
    command.downgrade(config, "0002_athletes")
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT score FROM rpe_entries WHERE workout_id=1").fetchone() == (7,)
