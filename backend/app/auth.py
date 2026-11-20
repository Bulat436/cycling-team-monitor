from secrets import token_urlsafe

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import delete, select, text

from .models import AuthSession, Invitation, User, now
from .rate_limit import reserve_login_attempt
from .schemas import AcceptAccess, Login, SessionInfo, normalize_email
from .security import (
    DB, Auth, audit, csrf_for, dummy_hash, passwords, token_hash, verify_password,
)

router = APIRouter(prefix="/auth", tags=["Авторизация"])


@router.post("/login", response_model=SessionInfo)
def login(payload: Login, request: Request, response: Response, db: DB):
    email = normalize_email(payload.email)
    ip = request.client.host if request.client else "unknown"
    retry_after = reserve_login_attempt(db, ip, email, now())
    if retry_after:
        raise HTTPException(429, "login_limited", headers={"Retry-After": str(retry_after)})

    user = db.scalar(select(User).where(User.email == email))
    encoded = user.password_hash if user and user.active else None
    if not verify_password(encoded or dummy_hash, payload.password.get_secret_value()) or not encoded:
        raise HTTPException(401, "invalid_credentials")

    db.execute(text("BEGIN IMMEDIATE"))
    db.refresh(user)
    if not user.active or user.password_hash != encoded:
        raise HTTPException(401, "invalid_credentials")

    if passwords.check_needs_rehash(encoded):
        user.password_hash = passwords.hash(payload.password.get_secret_value())
    settings = request.app.state.settings
    lifetime = settings.session_hours * 3600
    previous_token = request.cookies.get(settings.cookie_name, "")
    timestamp = now()
    db.execute(delete(AuthSession).where(AuthSession.expires_at <= timestamp))
    if len(previous_token) == 43:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(previous_token)))
    token = token_urlsafe(32)
    db.add(AuthSession(token_hash=token_hash(token), user_id=user.id, expires_at=timestamp + lifetime))
    audit(db, "login", user.id)
    db.commit()

    response.set_cookie(
        key=settings.cookie_name, value=token, max_age=lifetime, path="/",
        httponly=True, secure=settings.secure_cookies, samesite="strict",
    )
    return {"user": user, "csrf_token": csrf_for(token)}


@router.get("/me", response_model=SessionInfo)
def me(auth: Auth):
    return {"user": auth.user, "csrf_token": auth.csrf_token}


@router.post("/logout", status_code=204)
def logout(request: Request, db: DB, auth: Auth):
    db.delete(auth.session)
    audit(db, "logout", auth.user.id)
    db.commit()
    settings = request.app.state.settings
    response = Response(status_code=204)
    response.delete_cookie(
        settings.cookie_name, path="/", secure=settings.secure_cookies, httponly=True, samesite="strict",
    )
    return response


@router.post("/access/accept", status_code=204)
def accept_access(payload: AcceptAccess, db: DB):
    timestamp = now()
    invitation = db.scalar(select(Invitation).where(
        Invitation.token_hash == token_hash(payload.token.get_secret_value()),
        Invitation.consumed_at.is_(None),
        Invitation.revoked_at.is_(None),
        Invitation.expires_at > timestamp,
    ))
    if not invitation:
        raise HTTPException(400, "access_invalid")

    user = db.get(User, invitation.user_id)
    if not user or not user.active or user.password_hash:
        raise HTTPException(400, "access_invalid")
    user.password_hash = passwords.hash(payload.password.get_secret_value())
    if user.activated_at is None:
        user.activated_at = timestamp
    invitation.consumed_at = timestamp
    audit(db, "access_accepted", user.id, user.id)
    db.commit()
