import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from secrets import token_urlsafe
from threading import Event
from time import monotonic, sleep, time

import httpx
import pytest
from alembic import command
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cli import bootstrap_coach, rotate_onelap_keys
from app.config import Settings
from app.main import create_app
from app.onelap_models import OnelapConnection, OnelapGate
from app.onelap_provider import ProviderError
from app.onelap_secrets import SessionVault
from conftest import COACH_EMAIL, ORIGIN
from fit_fixtures import cycling_fit
from test_athletes import API, invite, sign_in, write
from test_workout_import import upload

GATEWAY = "https://gateway.example"


@dataclass
class Gateway:
    login: str = field(default_factory=lambda: token_urlsafe(20))
    password: str = field(default_factory=lambda: token_urlsafe(24))
    access: str = field(default_factory=lambda: token_urlsafe(24))
    refresh: str = field(default_factory=lambda: token_urlsafe(24))
    subject: str = field(default_factory=lambda: token_urlsafe(24))
    started_at: int = field(default_factory=lambda: int(time()) - 86400)
    rides: list = field(default_factory=list)
    files: dict = field(default_factory=dict)
    seen: list = field(default_factory=list)
    login_status: int = 200
    refresh_status: int = 200
    list_error: str | None = None
    list_status: int | None = None
    required_token: str | None = None
    hold_download: tuple[Event, Event] | None = None
    second_page: list | None = None

    def session(self):
        return {"subject": self.subject, "access_token": self.access, "refresh_token": self.refresh}

    def handle(self, request):
        assert str(request.url.copy_with(path="/")) == GATEWAY + "/"
        path = request.url.path
        payload = json.loads(request.content) if request.content else {}
        self.seen.append((request.method, path, payload if path.startswith("/v1/rides") else {}))
        if path == "/v1/session" and request.method == "POST":
            assert payload == {"login": self.login, "password": self.password}
            return httpx.Response(self.login_status, json=self.session() if self.login_status == 200 else {"sensitive": self.password})
        if path == "/v1/session/refresh":
            assert payload["refresh_token"] == self.refresh
            return httpx.Response(self.refresh_status, json=self.session())
        expected = self.required_token or self.access
        if request.headers.get("Authorization") != "Bearer " + expected:
            return httpx.Response(401)
        if path == "/v1/session":
            return httpx.Response(204)
        if path == "/v1/rides":
            if self.list_error == "network":
                raise httpx.ConnectTimeout(self.password)
            if self.list_status == 429:
                return httpx.Response(429, headers={"Retry-After": "180"})
            if self.list_status is not None:
                return httpx.Response(self.list_status, headers={"Location": "https://other.example/private"})
            if self.second_page is not None:
                if payload.get("cursor") == "next-page":
                    return httpx.Response(200, json={"workouts": self.second_page})
                return httpx.Response(200, json={"workouts": self.rides, "cursor": "next-page"})
            return httpx.Response(200, json={"workouts": self.rides})
        if path == "/v1/rides/details":
            return httpx.Response(200, json=next(item for item in self.rides if item["id"] == payload["id"]))
        assert path == "/v1/rides/fit"
        if self.hold_download is not None:
            enter, resume = self.hold_download
            enter.set()
            assert resume.wait(timeout=8)
        content = self.files.get(payload["id"])
        return httpx.Response(200, content=content) if content else httpx.Response(404)

    def ride(self, name, *, revision="1", content=None, started_at=None):
        start = self.started_at if started_at is None else started_at
        self.files[name] = cycling_fit(start=start) if content is None else content
        return {"id": name, "revision": revision, "started_at": start}


def onelap_path(athlete_id):
    return f"/athletes/{athlete_id}/onelapfit"


def connect(client, csrf, gateway, days=30):
    return write(client, "POST", "/onelapfit/connect", {
        "login": gateway.login, "password": gateway.password, "history_days": days,
    }, csrf)


def fetch(client, path):
    result = client.get(API + path)
    assert result.status_code == 200, result.text
    return result.json()


@pytest.fixture
def rig(migration, password):
    path, config = migration
    command.upgrade(config, "head")
    gateway = Gateway()
    settings = Settings(
        database_path=path, fit_path=path.parent / "fit", allowed_origins=[ORIGIN],
        secure_cookies=True, serve_frontend=False, onelap_gateway=GATEWAY,
        onelap_keys=[SecretStr(Fernet.generate_key().decode())], onelap_request_interval_seconds=.25,
    )
    bootstrap_coach(settings, COACH_EMAIL, "Тренер", password)
    app = create_app(settings, onelap_transport=httpx.MockTransport(gateway.handle), start_onelap_worker=False)
    with TestClient(app, base_url=ORIGIN) as coach:
        _, coach_csrf = sign_in(coach, COACH_EMAIL, password)
        owner = TestClient(app, base_url=ORIGIN)
        other = TestClient(app, base_url=ORIGIN)
        try:
            owner_id, owner_csrf = invite(coach, coach_csrf, owner, "owner@example.com", password)
            other_id, other_csrf = invite(coach, coach_csrf, other, "other@example.com", password)
            yield app, settings, gateway, (coach, coach_csrf), (owner, owner_id, owner_csrf), (other, other_id, other_csrf)
        finally:
            owner.close()
            other.close()


def make_due(app, athlete_id):
    with Session(app.state.engine) as db:
        db.get(OnelapConnection, athlete_id).next_sync_at = time() - 1
        db.commit()


def test_initial_incremental_duplicate_partial_and_correction(rig):
    app, _, gateway, (coach, coach_csrf), (owner, athlete_id, csrf), (other, _, other_csrf) = rig
    path = onelap_path(athlete_id)
    assert connect(coach, coach_csrf, gateway).status_code == 403
    assert connect(owner, None, gateway).status_code == 403
    assert other.get(API + path).status_code == 404
    assert other.get(API + path + "/runs").status_code == 404
    assert write(other, "POST", path + "/sync", None, other_csrf).status_code == 404
    assert fetch(owner, path)["status"] == "not_connected"

    good = gateway.ride("known")
    bad = bytearray(cycling_fit(start=gateway.started_at))
    bad[-1] ^= 1
    damaged = gateway.ride("corrupt", content=bytes(bad))
    gateway.rides = [good]
    gateway.second_page = [good, damaged, gateway.ride("old", started_at=gateway.started_at - 60 * 86400)]
    assert connect(owner, csrf, gateway, days=30).status_code == 204
    initial = fetch(coach, path)
    assert initial["status"] == "connected" and initial["validation_pending"]
    first = write(owner, "POST", path + "/sync", None, csrf)
    second = write(coach, "POST", path + "/sync", None, coach_csrf)
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert app.state.onelap.run_one()
    report = fetch(coach, path + "/runs")[0]
    assert (report["status"], report["imported"], report["rejected"]) == ("partial", 1, 1)
    assert report["errors"] == ["fit_bad_crc"]
    assert fetch(owner, path)["last_success_at"] is None
    assert any("started_from" in payload and "changed_from" not in payload for _, endpoint, payload in gateway.seen if endpoint == "/v1/rides")

    make_due(app, athlete_id)
    gateway.second_page = None
    gateway.rides = [good, damaged]
    assert app.state.onelap.run_one()
    assert fetch(owner, path)["status"] == "synced"
    assert fetch(owner, path)["last_success_at"] is not None
    assert fetch(owner, path + "/runs")[0]["imported"] == 0
    assert len([1 for _, endpoint, _ in gateway.seen if endpoint == "/v1/rides/fit"]) == 2
    assert any("changed_from" in payload and "started_from" not in payload for _, endpoint, payload in gateway.seen if endpoint == "/v1/rides")
    assert app.state.onelap.provider.workout_metadata(app.state.onelap.vault.open(
        athlete_id, 1, read_secret(app, athlete_id),
    ), "known").id == "known"

    make_due(app, athlete_id)
    gateway.rides = [gateway.ride("known", revision="2", content=cycling_fit(start=gateway.started_at, power=231))]
    assert app.state.onelap.run_one()
    workouts = fetch(owner, f"/athletes/{athlete_id}/workouts")["workouts"]
    assert len(workouts) == 1 and workouts[0]["np"] == pytest.approx(231)
    assert workouts[0]["summary"]["sample_count"] == 120


def read_secret(app, athlete_id):
    with Session(app.state.engine) as db:
        return db.get(OnelapConnection, athlete_id).secret


@pytest.mark.parametrize(("status", "expected", "code"), [
    (401, 422, "invalid_credentials"),
    (409, 409, "interaction_required"),
    (503, 503, "network_error"),
])
def test_login_errors_are_sanitized_and_throttled(rig, status, expected, code):
    app, _, gateway, _, (owner, athlete_id, csrf), _ = rig
    gateway.login_status = status
    response = connect(owner, csrf, gateway)
    assert response.status_code == expected and response.json() == {"detail": "onelap_" + code}
    state = fetch(owner, onelap_path(athlete_id))
    assert state["diagnostic"] == code and not state["has_session"]
    assert read_secret(app, athlete_id) is None
    assert gateway.password not in json.dumps(state)
    assert connect(owner, csrf, gateway).status_code == 429
    assert not app.state.onelap.run_one()


def test_refresh_success_and_reauthorization_remove_ciphertext(rig):
    app, _, gateway, _, (owner, athlete_id, csrf), _ = rig
    path = onelap_path(athlete_id)
    assert connect(owner, csrf, gateway).status_code == 204
    first = read_secret(app, athlete_id)
    gateway.access = token_urlsafe(24)
    issue = write(owner, "POST", path + "/test", None, csrf)
    assert issue.status_code == 202
    assert app.state.onelap.run_one()
    assert read_secret(app, athlete_id) != first
    assert fetch(owner, path)["last_success_at"] is None
    assert fetch(owner, path + "/runs")[0]["trigger"] == "verify"
    assert [path for _, path, _ in gateway.seen].count("/v1/session/refresh") == 1

    gateway.access = token_urlsafe(24)
    gateway.refresh_status = 401
    assert app.state.onelap.run_one()
    state = fetch(owner, path)
    assert state["status"] == "reauthorization" and not state["has_session"]
    assert read_secret(app, athlete_id) is None
    assert not app.state.onelap.run_one()


def test_network_retry_rate_limit_and_no_redirect(rig):
    app, _, gateway, _, (owner, athlete_id, csrf), (other, _, other_csrf) = rig
    path = onelap_path(athlete_id)
    assert connect(owner, csrf, gateway).status_code == 204
    gateway.list_error = "network"
    assert app.state.onelap.run_one()
    state = fetch(owner, path)
    assert state["diagnostic"] == "network_error" and state["next_sync_at"] > time() + 30
    assert gateway.password not in str(fetch(owner, path + "/runs"))
    assert not app.state.onelap.run_one()

    make_due(app, athlete_id)
    gateway.list_error = None
    gateway.list_status = 429
    assert app.state.onelap.run_one()
    state = fetch(owner, path)
    assert state["diagnostic"] == "rate_limited" and state["next_sync_at"] > time() + 160
    assert write(owner, "POST", path + "/sync", None, csrf).status_code == 429
    assert connect(other, other_csrf, gateway).status_code == 429
    with Session(app.state.engine) as db:
        db.get(OnelapGate, 1).blocked_until = 0
        db.commit()

    make_due(app, athlete_id)
    gateway.list_status = 302
    assert app.state.onelap.run_one()
    assert fetch(owner, path)["diagnostic"] == "invalid_response"


def test_disconnect_fences_in_flight_import_and_revokes(rig):
    app, _, gateway, _, (owner, athlete_id, csrf), _ = rig
    path = onelap_path(athlete_id)
    gateway.rides = [gateway.ride("open")]
    assert connect(owner, csrf, gateway).status_code == 204
    downloading, resume = Event(), Event()
    gateway.hold_download = downloading, resume
    with ThreadPoolExecutor(max_workers=1) as executor:
        task = executor.submit(app.state.onelap.run_one)
        assert downloading.wait(timeout=8)
        try:
            assert write(owner, "POST", "/onelapfit/disconnect", None, csrf).status_code == 204
            assert read_secret(app, athlete_id) is None
        finally:
            resume.set()
        assert task.result(timeout=8)
    assert fetch(owner, path)["status"] == "not_connected"
    assert fetch(owner, path + "/runs")[0]["status"] == "cancelled"
    assert fetch(owner, f"/athletes/{athlete_id}/workouts")["workouts"] == []
    assert any(method == "DELETE" and url == "/v1/session" for method, url, _ in gateway.seen)


def test_restart_recovers_run_and_lock_excludes_second_process(rig):
    app, settings, gateway, _, (owner, athlete_id, csrf), _ = rig
    path = onelap_path(athlete_id)
    gateway.rides = [gateway.ride("missed")]
    assert connect(owner, csrf, gateway).status_code == 204
    assert app.state.onelap._claim() is not None
    assert fetch(owner, path)["status"] == "syncing"

    lock_path = app.state.onelap_scheduler.lock_path
    script = """
import portalocker, sys
try:
    with portalocker.Lock(sys.argv[1], mode="a", timeout=0, fail_when_locked=True):
        sys.exit(0)
except portalocker.exceptions.AlreadyLocked:
    sys.exit(3)
"""
    with app.state.onelap_scheduler.leader_lock():
        assert subprocess.run([sys.executable, "-c", script, str(lock_path)], timeout=8).returncode == 3

    restarted = create_app(settings, onelap_transport=httpx.MockTransport(gateway.handle))
    with TestClient(restarted, base_url=ORIGIN):
        deadline = monotonic() + 10
        while monotonic() < deadline and fetch(owner, path)["status"] != "synced":
            sleep(.1)
        assert fetch(owner, path)["status"] == "synced"
    report = fetch(owner, path + "/runs")
    assert [item["status"] for item in report] == ["complete", "interrupted"]


def test_encryption_rotation_and_origin_binding(rig):
    app, settings, gateway, (coach, coach_csrf), (owner, athlete_id, csrf), _ = rig
    assert connect(owner, csrf, gateway).status_code == 204
    old = read_secret(app, athlete_id)
    assert all(secret not in old for secret in (gateway.login, gateway.password, gateway.subject, gateway.access, gateway.refresh))
    with pytest.raises(ProviderError, match="key_unavailable"):
        SessionVault(settings.onelap_keys, "https://different.example/").open(athlete_id, 1, old)
    with pytest.raises(ProviderError, match="key_unavailable"):
        app.state.onelap.vault.open(athlete_id + 1, 1, old)
    with pytest.raises(ValueError, match="Ротация отменена"):
        rotate_onelap_keys(settings.model_copy(update={
            "onelap_keys": [SecretStr(Fernet.generate_key().decode()), SecretStr(Fernet.generate_key().decode())],
        }))
    assert old == read_secret(app, athlete_id)
    new = SecretStr(Fernet.generate_key().decode())
    assert rotate_onelap_keys(settings.model_copy(update={"onelap_keys": [new, *settings.onelap_keys]})) == 1
    assert SessionVault([new], GATEWAY + "/").open(athlete_id, 1, read_secret(app, athlete_id)).subject == gateway.subject
    assert write(coach, "POST", f"/admin/users/{athlete_id}/deactivate", None, coach_csrf).status_code == 204
    assert read_secret(app, athlete_id) is None


def test_empty_then_manual_fit_deduplicates_and_sets_regular_interval(rig):
    app, settings, gateway, _, (owner, athlete_id, csrf), _ = rig
    path = onelap_path(athlete_id)
    assert connect(owner, csrf, gateway).status_code == 204
    assert app.state.onelap.run_one()
    state = fetch(owner, path)
    assert state["status"] == "synced"
    assert state["next_sync_at"] - state["last_success_at"] == settings.onelap_interval_seconds == 900
    assert not fetch(owner, f"/athletes/{athlete_id}/workouts")["workouts"]

    payload = cycling_fit(start=gateway.started_at)
    original = upload(owner, payload, csrf).json()
    make_due(app, athlete_id)
    gateway.rides = [gateway.ride("same", content=payload)]
    assert app.state.onelap.run_one()
    assert fetch(owner, path + "/runs")[0]["duplicates"] == 1
    workouts = fetch(owner, f"/athletes/{athlete_id}/workouts")["workouts"]
    assert len(workouts) == 1 and workouts[0]["id"] == original["workout_id"]


def test_gateway_subject_cannot_be_linked_to_two_athletes(rig):
    app, _, gateway, _, (owner, _, csrf), (other, other_id, other_csrf) = rig
    assert connect(owner, csrf, gateway).status_code == 204
    rejected = connect(other, other_csrf, gateway)
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "onelap_account_already_linked"
    assert read_secret(app, other_id) is None


def test_default_gateway_is_disabled_without_retaining_login(client, password):
    _, coach_csrf = sign_in(client, COACH_EMAIL, password)
    owner = TestClient(client.app, base_url=ORIGIN)
    try:
        athlete_id, csrf = invite(client, coach_csrf, owner, "disabled@example.com", password)
        gateway = Gateway()
        response = connect(owner, csrf, gateway)
        assert response.status_code == 503
        assert response.json()["detail"] == "onelap_provider_unavailable"
        status = fetch(owner, onelap_path(athlete_id))
        assert not status["available"] and not status["has_session"]
        assert status["status"] == "unavailable"
        with Session(client.app.state.engine) as db:
            assert db.get(OnelapConnection, athlete_id) is None
    finally:
        owner.close()
