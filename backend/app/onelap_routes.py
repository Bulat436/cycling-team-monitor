from ipaddress import ip_address
from math import ceil
from time import time
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select

from .athletes import AthleteScope
from .fit_parser import FitProblem
from .onelap_models import OnelapConnection, SyncError, SyncRun
from .onelap_provider import ProviderError
from .security import DB, Actor, Auth

router = APIRouter(tags=["OnelapFit"])
account_path = "/athletes/{athlete_id}/onelapfit"


class ConnectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login: SecretStr = Field(min_length=1, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=512)
    history_days: Annotated[int, Field(strict=True, ge=1, le=365)] | None = None


class ConnectionView(BaseModel):
    available: bool
    validation_pending: bool
    status: str
    diagnostic: str | None
    has_session: bool
    can_disconnect: bool
    history_days: int
    last_success_at: float | None
    next_sync_at: float | None


class RunView(BaseModel):
    id: int
    trigger: str
    status: str
    created_at: float
    started_at: float | None
    ended_at: float | None
    imported: int
    duplicates: int
    rejected: int
    diagnostic: str | None
    errors: list[str]


def https_or_loopback(request: Request):
    if request.url.scheme == "https":
        return
    if request.client and request.url.hostname in {"localhost", "127.0.0.1", "::1"}:
        try:
            if ip_address(request.client.host).is_loopback:
                return
        except ValueError:
            pass
    raise HTTPException(403, "onelap_https_required")


def response_error(error: FitProblem | ProviderError) -> HTTPException:
    if isinstance(error, FitProblem):
        return HTTPException(error.status, error.code)
    status = {
        "invalid_credentials": 422, "interaction_required": 409, "sync_busy": 409,
        "not_connected": 409, "connection_changed": 409, "already_connected": 409, "account_already_linked": 409,
        "role_denied": 403, "rate_limited": 429, "sync_backoff": 429,
    }.get(error.code, 503)
    headers = {"Retry-After": str(max(1, ceil(error.retry_at - time())))} if error.retry_at is not None else None
    return HTTPException(status, error.code if error.code == "role_denied" else "onelap_" + error.code, headers=headers)


def connection_view(service, connection: OnelapConnection | None) -> ConnectionView:
    return ConnectionView(
        available=service.available,
        validation_pending=service.available and not service.validated,
        status=connection.status if connection and service.available else ("not_connected" if service.available else "unavailable"),
        diagnostic=(connection.diagnostic if connection else None) if service.available else (
            "provider_unavailable" if service.provider is None else "key_unavailable"
        ),
        has_session=bool(connection and connection.secret),
        can_disconnect=bool(connection and (connection.account_hash or connection.secret or connection.status == "checking")),
        history_days=connection.history_days if connection else service.default_history,
        last_success_at=connection.last_success_at if connection else None,
        next_sync_at=connection.next_sync_at if connection and service.available else None,
    )


@router.get(account_path, response_model=ConnectionView)
def status(scope: AthleteScope, db: DB, request: Request):
    return connection_view(request.app.state.onelap, db.get(OnelapConnection, scope[0].athlete_id))


@router.post("/onelapfit/connect", status_code=204)
def connect(payload: ConnectInput, actor: Actor, request: Request):
    if actor.role != "athlete":
        raise HTTPException(403, "role_denied")
    https_or_loopback(request)
    service = request.app.state.onelap
    try:
        service.connect(actor, payload.login.get_secret_value(), payload.password.get_secret_value(),
                        payload.history_days or service.default_history)
    except (FitProblem, ProviderError) as exc:
        raise response_error(exc) from None


@router.post("/onelapfit/disconnect", status_code=204)
def disconnect(actor: Actor, request: Request):
    try:
        request.app.state.onelap.disconnect(actor)
    except (FitProblem, ProviderError) as exc:
        raise response_error(exc) from None


def schedule(request, scope, db, auth, trigger):
    try:
        run_id = request.app.state.onelap.enqueue(db, scope[0].athlete_id, trigger, auth.user.id)
        db.commit()
        return {"run_id": run_id}
    except ProviderError as exc:
        raise response_error(exc) from None


@router.post(account_path + "/sync", status_code=202)
def sync_now(scope: AthleteScope, db: DB, auth: Auth, request: Request) -> dict[str, int]:
    return schedule(request, scope, db, auth, "manual")


@router.post(account_path + "/test", status_code=202)
def test_connection(scope: AthleteScope, db: DB, auth: Auth, request: Request) -> dict[str, int]:
    return schedule(request, scope, db, auth, "verify")


@router.get(account_path + "/runs", response_model=list[RunView])
def runs(scope: AthleteScope, db: DB, limit: Annotated[int, Query(ge=1, le=100)] = 30):
    history = db.scalars(select(SyncRun).where(SyncRun.athlete_id == scope[0].athlete_id)
                         .order_by(SyncRun.id.desc()).limit(limit)).all()
    codes: dict[int, list[str]] = {}
    for run_id, code in db.execute(select(SyncError.run_id, SyncError.code)
                                    .where(SyncError.run_id.in_([run.id for run in history])).distinct()):
        codes.setdefault(run_id, []).append(code)
    return [
        RunView(
            id=run.id, trigger=run.trigger, status=run.status, created_at=run.created_at,
            started_at=run.started_at, ended_at=run.ended_at, imported=run.imported,
            duplicates=run.duplicates, rejected=run.rejected, diagnostic=run.diagnostic,
            errors=sorted(codes.get(run.id, [])),
        )
        for run in history
    ]
