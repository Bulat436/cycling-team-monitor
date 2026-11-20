from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from time import time
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from app import analytics as coach_analytics
from app.athletes import local_date
from app.backup import create_backup, restore_backup, verify_backup
from app.config import ROOT, Settings
from app.main import create_app
from conftest import ORIGIN
from fit_fixtures import cycling_fit
import seed_team
from test_access import accept, invite, login, post
from test_athletes import API, DIARY, write
from test_workout_import import upload


def success(response, status=200):
    assert response.status_code == status, response.text
    return response.json() if response.content else None


def test_team_of_twenty_has_real_imports_and_three_equal_coaches(tmp_path, password, monkeypatch):
    stamp = datetime(2026, 1, 15, 12, tzinfo=UTC).timestamp()
    monkeypatch.setattr(seed_team, "time", lambda: stamp)
    monkeypatch.setattr(coach_analytics, "time", lambda: stamp)
    settings = seed_team.seed_team(tmp_path / "demo", password).model_copy(update={
        "serve_frontend": False, "secure_cookies": True, "allowed_origins": [ORIGIN],
    })
    team_views = []
    with TestClient(create_app(settings), base_url=ORIGIN) as api:
        for number in (1, 2, 3):
            success(login(api, password, f"coach{number:02}@example.com"))
            team_views.append(success(api.get(API + "/analytics/team?period=28")))
        first = team_views[0]
        assert len(first["rows"]) == first["total"] == 20
        assert first["active"] == 20 and first["filled"] == 16
        assert all(view == first for view in team_views)
        assert first["alerted"] > 0
        assert len(success(api.get(API + "/analytics/team?group=Трек"))["rows"]) == 10
        row = first["rows"][0]
        athlete_path = f"/athletes/{row['athlete_id']}"
        analytics = success(api.get(API + f"/analytics{athlete_path}?period=28"))
        assert len(analytics["workouts"]) == 4
        assert len(analytics["ftp"]) >= 2 and analytics["power_zones"]["bands"]
        ride = analytics["workouts"][-1]
        record = success(api.get(API + f"/workouts/{ride['id']}"))
        assert ride["np"] == record["np"] and ride["tss"] == record["tss"]
        assert success(api.get(API + f"{athlete_path}/onelapfit"))["available"] is False
        assert success(api.get(API + f"{athlete_path}/onelapfit/runs"))

        outsider = success(login(api, password, "athlete20@example.com"))
        csrf = outsider["csrf_token"]
        for path in (athlete_path, athlete_path + "/wellness", athlete_path + "/workouts", athlete_path + "/ftp",
                     athlete_path + "/zones/power", athlete_path + "/weight", athlete_path + "/onelapfit", athlete_path + "/onelapfit/runs"):
            success(api.get(API + path), 404)
        for suffix in ("", "/fit", "/metrics", "/samples", "/series", "/rpe"):
            success(api.get(API + f"/workouts/{ride['id']}" + suffix), 404)
        success(write(api, "PUT", f"/workouts/{ride['id']}/rpe", {"score": 2}, csrf), 404)
        success(post(api, athlete_path + "/onelapfit/sync", csrf=csrf), 404)
        success(api.get(API + "/admin/audit"), 403)
        success(api.get(API + "/analytics/team"), 403)
    with pytest.raises(FileExistsError):
        seed_team.seed_team(tmp_path / "demo", password)


def test_end_to_end_backup_recovery_keeps_wal_and_fit_but_resets_access(client, password, tmp_path):
    settings = client.app.state.settings
    coach_csrf = success(login(client, password))["csrf_token"]
    with TestClient(create_app(settings), base_url=ORIGIN) as athlete:
        invitation = invite(client, coach_csrf)
        token = invitation["token"]
        success(accept(athlete, token, password), 204)
        owner = success(login(athlete, password, "athlete@example.com"))
        athlete_id = owner["user"]["id"]
        csrf = owner["csrf_token"]
        now = int(time())
        day = local_date("Europe/Moscow", now).isoformat()
        scope = f"/athletes/{athlete_id}"
        success(write(athlete, "PUT", f"{scope}/wellness/{day}", DIARY, csrf))
        success(write(client, "PUT", f"{scope}/ftp/{day}", {"watts": 250}, coach_csrf))
        original = cycling_fit(start=now)
        workout_id = success(upload(athlete, original, csrf))["workout_id"]
        assert success(upload(athlete, original, csrf))["status"] == "duplicate"
        ride_path = f"/workouts/{workout_id}"
        assert success(athlete.get(API + ride_path))["np"] == pytest.approx(200)
        success(write(athlete, "PUT", ride_path + "/rpe", {"score": 8}, csrf))
        coach_data = success(client.get(API + f"/analytics{scope}?period=7"))
        assert coach_data["workouts"][0]["tss"] == pytest.approx(120 / 3600 * .8 ** 2 * 100)
        assert coach_data["workouts"][0]["rpe"] == 8
        assert coach_data["wellness"]
        success(post(athlete, scope + "/onelapfit/sync", csrf=csrf), 503)

        pending_invite = invite(client, coach_csrf, email="waiting@example.com")
        # An open reader keeps these committed writes in the WAL during the backup.
        with closing(sqlite3.connect(settings.database_file)) as reader, closing(sqlite3.connect(settings.database_file)) as writer:
            reader.execute("BEGIN")
            reader.execute("SELECT count(*) FROM users").fetchone()
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute(
                "INSERT INTO onelap_connections (athlete_id,generation,status,secret,history_days,failures,created_at,updated_at)"
                " VALUES (?,1,'connected','opaque-synthetic-session',90,0,?,?)", (athlete_id, now, now),
            )
            writer.execute("UPDATE users SET name='Имя из WAL' WHERE id=?", (athlete_id,))
            writer.commit()
            assert Path(str(settings.database_file) + "-wal").stat().st_size > 32
            archive = tmp_path / "backup.zip"
            create_backup(settings, archive)
            assert writer.execute("SELECT secret FROM onelap_connections").fetchone()[0]
        verify_backup(archive)
        assert success(athlete.get(API + "/auth/me"))["user"]["id"] == athlete_id
        with ZipFile(archive) as backup:
            database = backup.read("team.sqlite3")
            assert b"opaque-synthetic-session" not in database
            fit_names = [name for name in backup.namelist() if name.startswith(f"fit/{athlete_id}/")]
            assert len(fit_names) == 1
            assert set(backup.namelist()) == {"team.sqlite3", "backup.json", *fit_names}
            damaged = tmp_path / "damaged.zip"
            with ZipFile(damaged, "w") as candidate:
                for name in ("team.sqlite3", "backup.json"):
                    candidate.writestr(name, backup.read(name))
                candidate.writestr(fit_names[0], b"x" + original[1:])
        with pytest.raises(ValueError, match="контрольная сумма"):
            verify_backup(damaged)
        restored = tmp_path / "restored"
        restore_backup(archive, restored)
        with pytest.raises(FileExistsError):
            restore_backup(archive, restored)
        with pytest.raises(FileExistsError):
            create_backup(settings, archive)
        restored_settings = settings.model_copy(update={
            "database_path": restored / "team.sqlite3", "fit_path": restored / "fit",
        })
        with TestClient(create_app(restored_settings), base_url=ORIGIN, cookies=athlete.cookies) as restarted:
            success(restarted.get(API + "/auth/me"), 401)
            success(accept(restarted, pending_invite["token"], password), 400)
            assert success(login(restarted, password, "athlete@example.com"))["user"]["name"] == "Имя из WAL"
            fit = restarted.get(API + ride_path + "/fit")
            assert fit.status_code == 200 and fit.content == original
            assert fit.headers["cache-control"] == "no-store"
            assert success(restarted.get(API + ride_path + "/rpe"))["score"] == 8
            assert success(restarted.get(API + f"{scope}/wellness/{day}"))["comment"] == DIARY["comment"]
            assert not success(restarted.get(API + f"{scope}/onelapfit"))["has_session"]


def test_restore_rejects_bad_archives_without_publishing_data(tmp_path):
    path = tmp_path / "invalid.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("team.sqlite3", b"test")
        archive.writestr("backup.json", b"{}")
        archive.writestr("unknown.bin", b"unexpected")
    with pytest.raises(ValueError, match="Недопустимый путь"):
        restore_backup(path, tmp_path / "restore")
    assert not (tmp_path / "restore").exists()

def test_private_data_cannot_use_frontend_directory(tmp_path):
    public = ROOT / "frontend" / "dist" / "assets"
    settings = Settings(_env_file=None, database_path=public / "team.sqlite3", fit_path=public / "fit")
    with pytest.raises(ValueError, match="outside the frontend"):
        create_app(settings)
    with pytest.raises(ValueError, match="outside the frontend"):
        _ = settings.fit_directory
    with pytest.raises(ValueError, match="outside the frontend"):
        create_backup(settings, public / "team.zip")
    with pytest.raises(ValueError, match="outside the frontend"):
        restore_backup(tmp_path / "archive.zip", public / "restored")
