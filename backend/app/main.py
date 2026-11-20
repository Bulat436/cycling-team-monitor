from contextlib import asynccontextmanager
from threading import Event
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from . import admin, athletes, auth, onelap_routes, wellness, workout_fit_routes, workouts
from .config import ROOT, Settings
from .database import get_db, make_engine
from .athlete_models import Workout
from .onelap_models import OnelapConnection
from .onelap_provider import OnelapFitProvider
from .onelap_scheduler import OnelapScheduler, RequestGate
from .onelap_secrets import SessionVault
from .onelap_sync import OnelapSyncService
from .workout_import import WorkoutImportService


def create_app(
    settings: Settings | None = None, *, onelap_transport=None, start_onelap_worker: bool = True,
) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(settings)
    importer = WorkoutImportService(engine, settings)
    stopped = Event()
    provider = None
    if settings.onelap_gateway:
        try:
            provider = OnelapFitProvider(
                settings.onelap_gateway, RequestGate(engine, settings.onelap_request_interval_seconds, stopped),
                settings.fit_max_bytes, transport=onelap_transport,
            )
        except ValueError:
            # Integration configuration must not prevent startup of the rest of the application.
            pass
    vault = SessionVault(settings.onelap_keys, provider.origin if provider else "")
    onelap = OnelapSyncService(engine, provider, vault, importer, settings, stopped)
    database_file = settings.database_file.resolve()
    scheduler = OnelapScheduler(onelap, database_file.with_name(database_file.name + ".onelap.lock"), stopped)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if onelap.available and start_onelap_worker:
            scheduler.start()
        try:
            yield
        finally:
            scheduler.close()
            if provider is not None and not scheduler.thread.is_alive():
                provider.close()
            engine.dispose()

    app = FastAPI(
        title="Cycling Team Monitor", version="0.4.0",
        docs_url="/api/v1/docs", redoc_url=None, openapi_url="/api/v1/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.workout_import = importer
    app.state.onelap = onelap
    app.state.onelap_scheduler = scheduler

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Validation errors must never echo passwords, invitation tokens or bodies.
        return JSONResponse(status_code=422, content={"detail": "validation_error"})

    @app.get("/api/v1/health", tags=["Состояние"])
    def health(db: Annotated[Session, Depends(get_db)]):
        try:
            db.execute(select(Workout.file_sha256).limit(1))
            db.execute(select(OnelapConnection.athlete_id).limit(1))
        except SQLAlchemyError as exc:
            raise HTTPException(503, "database_unavailable") from exc
        return {"status": "ok"}

    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(admin.router, prefix="/api/v1")
    app.include_router(athletes.router, prefix="/api/v1")
    app.include_router(wellness.router, prefix="/api/v1")
    app.include_router(workout_fit_routes.router, prefix="/api/v1")
    app.include_router(workouts.router, prefix="/api/v1")
    app.include_router(onelap_routes.router, prefix="/api/v1")

    if settings.serve_frontend:
        dist = ROOT / "frontend" / "dist"
        if not (dist / "index.html").is_file():
            raise RuntimeError("Frontend build is missing. Run npm run build in frontend.")
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str):
            if path == "api" or path.startswith("api/"):
                raise HTTPException(404, "not_found")
            return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    return app
