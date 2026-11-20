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

from . import admin, analytics, athletes, auth, onelap_routes, wellness, workout_fit_routes, workout_series, workouts
from .config import ROOT, Settings
from .database import get_db, make_engine
from .analytics_models import AppSetting
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
        title="Cycling Team Monitor", version="1.0.0rc1",
        docs_url="/api/v1/docs" if settings.expose_api_docs else None, redoc_url=None,
        openapi_url="/api/v1/openapi.json" if settings.expose_api_docs else None,
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
            db.execute(select(AppSetting.key).limit(1))
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
    app.include_router(analytics.router, prefix="/api/v1")
    app.include_router(workout_series.router, prefix="/api/v1")

    if settings.serve_frontend:
        dist = ROOT / "frontend" / "dist"
        for name in ("index.html", "sw.js", "manifest.webmanifest"):
            if not (dist / name).is_file():
                raise RuntimeError("Production PWA is incomplete. Run npm run build in frontend.")
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
        app.mount("/icons", StaticFiles(directory=dist / "icons"), name="icons")

        @app.get("/sw.js", include_in_schema=False)
        def service_worker():
            return FileResponse(dist / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})

        @app.get("/manifest.webmanifest", include_in_schema=False)
        def web_manifest():
            return FileResponse(dist / "manifest.webmanifest", media_type="application/manifest+json",
                                headers={"Cache-Control": "no-cache"})

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str):
            if path.split("/", 1)[0] in {"api", "data", "fit"}:
                raise HTTPException(404, "not_found")
            return FileResponse(dist / "index.html", headers={
                "Cache-Control": "no-cache",
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                    "img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
                    "form-action 'self'"
                ),
            })

    return app
