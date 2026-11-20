from secrets import token_urlsafe
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .athlete_models import AthleteProfile
from .models import AuditLog, AuthSession, Invitation, User, now
from .schemas import (
    AccessLink, Account, Action, AuditEntry, CreateInvitation, InvitationInfo, normalize_email,
)
from .onelap_models import OnelapConnection
from .onelap_sync import cancel_connection
from .security import DB, Coach, audit, revoke_access_links, token_hash

router = APIRouter(prefix="/admin", tags=["Управление доступом"])


def get_account(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "account_missing")
    return user


def issue_link(db: Session, user: User, coach: User, request: Request, kind: Action) -> AccessLink:
    revoke_access_links(db, user.id)
    settings = request.app.state.settings
    hours = settings.invitation_hours if kind == "activation" else settings.reset_hours
    token = token_urlsafe(32)
    link = Invitation(
        user_id=user.id, issued_by=coach.id, token_hash=token_hash(token),
        kind=kind, expires_at=now() + hours * 3600,
    )
    db.add(link)
    audit(db, "invitation_issued" if kind == "activation" else "password_reset_issued", coach.id, user.id)
    db.flush()
    result = AccessLink(id=link.id, kind=kind, token=token, expires_at=link.expires_at)
    db.commit()
    return result


@router.get("/users", response_model=list[Account])
def list_users(db: DB, coach: Coach):
    return db.scalars(select(User).order_by(User.name, User.id)).all()


@router.get("/invitations", response_model=list[InvitationInfo])
def list_invitations(db: DB, coach: Coach, offset: Annotated[int, Query(ge=0)] = 0):
    return db.scalars(select(Invitation).order_by(Invitation.id.desc()).offset(offset).limit(100)).all()


@router.post("/invitations", status_code=201, response_model=AccessLink)
def invite(payload: CreateInvitation, request: Request, db: DB, coach: Coach):
    email = normalize_email(payload.email)
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(409, "email_exists")
    user = User(email=email, name=payload.name, role=payload.role)
    db.add(user)
    db.flush()
    if user.role == "athlete":
        db.add(AthleteProfile(athlete_id=user.id))
    return issue_link(db, user, coach, request, "activation")


@router.post("/users/{user_id}/invitation", status_code=201, response_model=AccessLink)
def renew_invitation(user_id: int, request: Request, db: DB, coach: Coach):
    user = get_account(db, user_id)
    if not user.active or user.activated_at is not None:
        raise HTTPException(409, "activation_unavailable")
    return issue_link(db, user, coach, request, "activation")


@router.post("/invitations/{invitation_id}/revoke", status_code=204)
def revoke_invitation(invitation_id: int, db: DB, coach: Coach):
    link = db.get(Invitation, invitation_id)
    if not link:
        raise HTTPException(404, "invitation_missing")
    if link.consumed_at or link.revoked_at or link.expires_at <= now():
        raise HTTPException(409, "invitation_inactive")
    link.revoked_at = now()
    audit(db, "invitation_revoked", coach.id, link.user_id)
    db.commit()


@router.post("/users/{user_id}/password-reset", status_code=201, response_model=AccessLink)
def reset_password(user_id: int, request: Request, db: DB, coach: Coach):
    user = get_account(db, user_id)
    if not user.active or user.activated_at is None or user.id == coach.id:
        raise HTTPException(409, "reset_unavailable")
    user.password_hash = None
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    return issue_link(db, user, coach, request, "reset")


@router.post("/users/{user_id}/deactivate", status_code=204)
def deactivate(user_id: int, db: DB, coach: Coach):
    user = get_account(db, user_id)
    if user.id == coach.id:
        raise HTTPException(409, "self_deactivation_denied")
    if user.active:
        user.active = False
        connection = db.get(OnelapConnection, user.id)
        if connection:
            cancel_connection(db, connection)
        revoke_access_links(db, user.id)
        db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
        audit(db, "account_deactivated", coach.id, user.id)
        db.commit()


@router.get("/audit", response_model=list[AuditEntry])
def list_audit(db: DB, coach: Coach, offset: Annotated[int, Query(ge=0)] = 0):
    return db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).offset(offset).limit(100)).all()
