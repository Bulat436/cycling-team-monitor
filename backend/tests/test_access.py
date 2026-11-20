import sqlite3
from concurrent.futures import ThreadPoolExecutor
from secrets import token_urlsafe

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.cli import bootstrap_coach, reset_coach_password
from app.main import create_app
from app.models import AuthSession, Invitation, User, now
from app.security import token_hash
from conftest import COACH_EMAIL, ORIGIN

ATHLETE_EMAIL = "athlete@example.com"


def post(client, path, payload=None, *, csrf=None, origin=ORIGIN):
    headers = {"Origin": origin} if origin else {}
    if csrf:
        headers["X-CSRF-Token"] = csrf
    return client.post("/api/v1" + path, headers=headers, json=payload)


def login(client, password, email=COACH_EMAIL, **kwargs):
    return post(client, "/auth/login", {"email": email, "password": password}, **kwargs)


def invite(client, csrf, *, email=ATHLETE_EMAIL, role="athlete"):
    response = post(client, "/admin/invitations", {
        "name": "Новый участник", "email": email, "role": role,
    }, csrf=csrf)
    assert response.status_code == 201, response.text
    return response.json()


def accept(client, token, password, **kwargs):
    return post(client, "/auth/access/accept", {"token": token, "password": password}, **kwargs)


def test_migrations_create_wal_and_round_trip(migration):
    path, config = migration
    command.upgrade(config, "head")
    command.check(config)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0001_access",)
    command.downgrade(config, "base")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name = 'users'").fetchone() is None
    command.upgrade(config, "head")


def test_health_registration_and_bootstrap(client, password):
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    assert client.get("/api/v1/openapi.json").status_code == 200
    assert post(client, "/auth/register", {}).status_code == 404
    with pytest.raises(ValueError, match="уже создан"):
        bootstrap_coach(client.app.state.settings, "other@example.com", "Тренер", password)


def test_session_cookie_csrf_logout_and_secret_storage(client, password):
    assert client.get("/api/v1/auth/me").status_code == 401
    assert login(client, password, origin=None).status_code == 403
    assert login(client, password, origin="https://evil.example.com").status_code == 403
    response = login(client, password, email=COACH_EMAIL.upper())
    assert response.status_code == 200, response.text
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("__Host-ctm_session=")
    assert all(flag in cookie for flag in ("HttpOnly", "Secure", "SameSite=strict", "Path=/"))
    assert response.headers["cache-control"] == "no-store"
    session = response.json()
    assert session["user"]["role"] == "coach"
    assert "password_hash" not in session["user"]
    raw_session = client.cookies["__Host-ctm_session"]
    with Session(client.app.state.engine) as db:
        assert db.get(AuthSession, raw_session) is None
        assert db.get(AuthSession, token_hash(raw_session)) is not None
        assert db.scalar(select(User.password_hash)).startswith("$argon2id$")

    csrf = session["csrf_token"]
    assert client.get("/api/v1/auth/me").json() == session
    assert post(client, "/auth/logout").status_code == 403
    assert post(client, "/auth/logout", csrf=csrf, origin="https://evil.example.com").status_code == 403
    assert post(client, "/auth/logout", csrf=csrf).status_code == 204
    client.cookies.set("__Host-ctm_session", raw_session)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_invitation_and_server_side_roles(client, password):
    coach = login(client, password).json()
    link = invite(client, coach["csrf_token"])
    assert login(client, password, ATHLETE_EMAIL).status_code == 401
    assert accept(client, link["token"], password, origin=None).status_code == 403
    assert accept(client, link["token"], password).status_code == 204
    assert accept(client, link["token"], password).status_code == 400
    stored = client.get("/api/v1/admin/invitations").json()[0]
    assert stored["consumed_at"] is not None
    assert {"token", "token_hash"}.isdisjoint(stored)
    assert post(client, "/auth/logout", csrf=coach["csrf_token"]).status_code == 204

    athlete = login(client, password, ATHLETE_EMAIL).json()
    assert athlete["user"]["role"] == "athlete"
    for route in ("users", "invitations", "audit"):
        assert client.get("/api/v1/admin/" + route).status_code == 403
    response = post(client, "/admin/invitations", {
        "name": "Чужой участник", "email": "extra@example.com",
    }, csrf=athlete["csrf_token"])
    assert response.status_code == 403


def test_revoked_expired_and_concurrent_acceptance(client, password):
    csrf = login(client, password).json()["csrf_token"]
    link = invite(client, csrf)
    assert post(client, f"/admin/invitations/{link['id']}/revoke", csrf=csrf).status_code == 204
    assert accept(client, link["token"], password).status_code == 400
    user_id = next(user["id"] for user in client.get("/api/v1/admin/users").json() if user["role"] == "athlete")
    route = f"/admin/users/{user_id}/invitation"
    expired = post(client, route, csrf=csrf).json()
    with Session(client.app.state.engine) as db:
        db.execute(update(Invitation).where(Invitation.id == expired["id"]).values(expires_at=now() - 1))
        db.commit()
    assert accept(client, expired["token"], password).status_code == 400
    link = post(client, route, csrf=csrf).json()

    def redeem():
        recipient = TestClient(client.app, base_url=ORIGIN)
        try:
            return accept(recipient, link["token"], password).status_code
        finally:
            recipient.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: redeem(), range(2))) == [204, 400]


def test_reset_revokes_sessions_and_deactivation_blocks_login(client, password):
    coach = login(client, password).json()
    csrf = coach["csrf_token"]
    link = invite(client, csrf)
    assert accept(client, link["token"], password).status_code == 204
    with TestClient(client.app, base_url=ORIGIN) as athlete_client:
        athlete = login(athlete_client, password, ATHLETE_EMAIL).json()["user"]
        route = f"/admin/users/{athlete['id']}"
        reset = post(client, route + "/password-reset", csrf=csrf)
        assert reset.status_code == 201
        assert athlete_client.get("/api/v1/auth/me").status_code == 401
        assert login(athlete_client, password, ATHLETE_EMAIL).status_code == 401
        next_password = token_urlsafe(24)
        assert accept(athlete_client, reset.json()["token"], next_password).status_code == 204
        assert login(athlete_client, next_password, ATHLETE_EMAIL).status_code == 200
        assert post(client, route + "/deactivate", csrf=csrf).status_code == 204
        assert athlete_client.get("/api/v1/auth/me").status_code == 401
        assert login(athlete_client, next_password, ATHLETE_EMAIL).status_code == 401

    own = coach["user"]["id"]
    assert post(client, f"/admin/users/{own}/deactivate", csrf=csrf).status_code == 409
    events = client.get("/api/v1/admin/audit")
    assert {"password_reset_issued", "account_deactivated"} <= {entry["action"] for entry in events.json()}
    assert all(value not in events.text for value in (password, next_password, reset.json()["token"]))


def test_another_coach_has_equal_permissions(client, password):
    coach = login(client, password).json()
    email = "second@example.com"
    link = invite(client, coach["csrf_token"], email=email, role="coach")
    assert accept(client, link["token"], password).status_code == 204
    second = login(client, password, email=email).json()
    assert second["user"]["role"] == "coach"
    assert client.get("/api/v1/admin/users").status_code == 200
    assert invite(client, second["csrf_token"])["kind"] == "activation"


def test_rate_limit_persists_between_app_instances(client, password):
    for _ in range(5):
        response = login(client, password, "unknown@example.com")
        assert response.status_code == 401
    with TestClient(create_app(client.app.state.settings), base_url=ORIGIN) as restarted:
        response = login(restarted, password, "unknown@example.com")
        assert response.status_code == 429
        assert 0 < int(response.headers["retry-after"]) <= 900
        assert login(restarted, password).status_code == 200


def test_session_expiry_and_untrusted_validation_body(client, password):
    login(client, password)
    with Session(client.app.state.engine) as db:
        db.execute(update(AuthSession).values(expires_at=now() - 1))
        db.commit()
    assert client.get("/api/v1/auth/me").status_code == 401
    response = post(client, "/auth/login", {"email": password, "password": { "secret": password }})
    assert response.status_code == 422
    assert password not in response.text



def test_operator_recovers_sole_coach_without_preserving_old_session(client, password):
    assert login(client, password).status_code == 200
    replacement = token_urlsafe(24)
    reset_coach_password(client.app.state.settings, COACH_EMAIL, replacement)
    assert client.get("/api/v1/auth/me").status_code == 401
    assert login(client, password).status_code == 401
    assert login(client, replacement).status_code == 200
    audit = client.get("/api/v1/admin/audit").json()
    assert any(item["action"] == "coach_password_reset_by_operator" for item in audit)
