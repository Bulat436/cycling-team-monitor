from dataclasses import dataclass
from hashlib import sha256
from hmac import compare_digest, digest
from secrets import token_urlsafe
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import update
from sqlalchemy.orm import Session

from .database import get_db
from .models import AuditLog, AuthSession, Invitation, User, now

passwords = PasswordHasher()
# A fixed dummy hash keeps verification work comparable when no account can sign in.
dummy_hash = passwords.hash(token_urlsafe(32))
DB = Annotated[Session, Depends(get_db)]


def token_hash(token: str) -> str:
    return sha256(token.encode()).hexdigest()


def csrf_for(token: str) -> str:
    return digest(token.encode(), b"ctm-session-csrf-v1", "sha256").hex()


def verify_password(encoded: str, password: str) -> bool:
    try:
        return passwords.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def audit(db: Session, action: str, actor_id: int | None, target_id: int | None = None):
    db.add(AuditLog(action=action, actor_id=actor_id, target_id=target_id))


def revoke_access_links(db: Session, user_id: int):
    timestamp = now()
    db.execute(update(Invitation).where(
        Invitation.user_id == user_id,
        Invitation.revoked_at.is_(None),
        Invitation.consumed_at.is_(None),
        Invitation.expires_at > timestamp,
    ).values(revoked_at=timestamp))


@dataclass
class Authenticated:
    user: User
    session: AuthSession
    csrf_token: str


def authenticate(request: Request, db: DB) -> Authenticated:
    token = request.cookies.get(request.app.state.settings.cookie_name, "")
    session = db.get(AuthSession, token_hash(token)) if len(token) == 43 else None
    user = db.get(User, session.user_id) if session and session.expires_at > now() else None
    if not user or not user.active or not user.password_hash:
        raise HTTPException(401, "authentication_required")
    csrf_token = csrf_for(token)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        supplied = request.headers.get("x-csrf-token", "").encode()
        if not compare_digest(supplied, csrf_token.encode()):
            raise HTTPException(403, "csrf_invalid")
    return Authenticated(user, session, csrf_token)


Auth = Annotated[Authenticated, Depends(authenticate)]


def require_coach(auth: Auth) -> User:
    if auth.user.role != "coach":
        raise HTTPException(403, "role_denied")
    return auth.user


Coach = Annotated[User, Depends(require_coach)]
