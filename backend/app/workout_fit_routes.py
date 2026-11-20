from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from .database import verify_origin
from .fit_parser import FitProblem
from .security import authenticate
from .workout_import import RequestActor
from .workout_schemas import ImportView, ReprocessOptions

router = APIRouter(tags=["Импорт FIT"])


def fit_actor(request: Request) -> RequestActor:
    verify_origin(request)
    # Release the read session before streaming the upload or decoding the file.
    with Session(request.app.state.engine) as db:
        auth = authenticate(request, db)
        return RequestActor(auth.user.id, auth.user.role, auth.session.token_hash)


Actor = Annotated[RequestActor, Depends(fit_actor)]


@router.post(
    "/workouts/import", response_model=ImportView,
    openapi_extra={"requestBody": {"required": True, "content": {
        "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
    }}},
)
async def manual_import(
    request: Request, actor: Actor,
    filename_header: Annotated[str, Header(alias="X-FIT-Filename", min_length=1, max_length=2048)],
):
    if actor.role != "athlete":
        raise HTTPException(403, "role_denied")
    service = request.app.state.workout_import
    filename = unquote(filename_header)
    if request.headers.get("content-type", "").split(";")[0].lower() != "application/octet-stream":
        raise HTTPException(415, "fit_content_type")
    try:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            length = int(content_length)
            if length < 0:
                raise ValueError("Negative upload length")
            if length > service.max_bytes:
                raise FitProblem("fit_too_large", 413)
        payload = bytearray()
        async for chunk in request.stream():
            if len(payload) + len(chunk) > service.max_bytes:
                raise FitProblem("fit_too_large", 413)
            payload.extend(chunk)
    except (ValueError, ClientDisconnect) as exc:
        raise HTTPException(400, "fit_invalid_structure") from exc
    except FitProblem as exc:
        try:
            await run_in_threadpool(service.log_failure, actor.user_id, filename, "manual", exc.code, actor)
        except FitProblem as access_error:
            raise HTTPException(access_error.status, access_error.code) from access_error
        raise HTTPException(exc.status, exc.code) from exc

    try:
        return await run_in_threadpool(
            service.import_fit, actor.user_id, payload, filename=filename, actor=actor,
        )
    except FitProblem as exc:
        raise HTTPException(exc.status, exc.code) from exc


@router.post("/workouts/{workout_id}/reprocess", status_code=204)
def reprocess_workout(workout_id: int, payload: ReprocessOptions, request: Request, actor: Actor):
    try:
        request.app.state.workout_import.reprocess(workout_id, actor, refresh_parameters=payload.refresh_parameters)
    except FitProblem as exc:
        raise HTTPException(exc.status, exc.code) from exc
