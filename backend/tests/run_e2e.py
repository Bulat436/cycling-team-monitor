"""Run the built application on a temporary SQLite database for Playwright."""

import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import time
from urllib.parse import urlsplit

import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.athlete_models import AthleteProfile, FtpRecord, HeartRateZoneProfile, PowerZoneProfile, WellnessEntry, Workout
from app.athletes import local_date
from app import wellness
from app.cli import bootstrap_coach
from app.config import Settings
from app.database import make_engine
from app import main as application
from app.models import User, now
from app.security import passwords
from app.training_intelligence import routes as training_routes
from app.workout_import import WorkoutImportService
from fit_fixtures import cycling_fit
from seed_training_pilot import AS_OF, seed_training_pilot


def seed_stage_b(settings: Settings, password: str) -> None:
    engine = make_engine(settings)
    try:
        password_hash = passwords.hash(password)
        started_at = int(datetime(2026, 1, 2, 7, tzinfo=UTC).timestamp())
        with Session(engine) as db:
            db.add(User(
                email="profiles-coach@example.com", name="Тренер профилей",
                role="coach", password_hash=password_hash, activated_at=now(),
            ))
            for project in ("windows-desktop", "android-viewport", "iphone-viewport", "coach-review"):
                athlete = User(
                    email=f"{project}@example.com", name=f"Спортсмен {project}",
                    role="athlete", password_hash=password_hash, activated_at=now(),
                )
                db.add(athlete)
                db.flush()
                profile = AthleteProfile(athlete_id=athlete.id)
                db.add(profile)
                db.flush()
                db.add_all([
                    Workout(athlete_id=athlete.id, started_at=started_at, timer_seconds=3600),
                    Workout(athlete_id=athlete.id, started_at=started_at + 3600, timer_seconds=None),
                ])
                if project == "coach-review":
                    db.add(WellnessEntry(
                        athlete_id=athlete.id, entry_date=local_date(profile.timezone), timezone=profile.timezone,
                        sleep_quality=4, general_fatigue=2, leg_fatigue=2, motivation=5, mood=4,
                        muscle_soreness=1, illness_symptoms=False,
                    ))
            db.commit()
    finally:
        engine.dispose()


def seed_analytics(settings: Settings, password: str) -> None:
    engine = make_engine(settings)
    start = int(time()) - 2 * 86400
    try:
        with Session(engine) as db:
            user = User(
                email="analytics-review@example.com", name="Спортсмен analytics-review", role="athlete",
                password_hash=passwords.hash(password), activated_at=now(),
            )
            db.add(user)
            db.flush()
            athlete_id = user.id
            db.add(AthleteProfile(athlete_id=athlete_id, team_group="Шоссе"))
            db.flush()
            db.add_all([
                FtpRecord(athlete_id=athlete_id, effective_from=local_date("Europe/Moscow", start), watts=250),
                PowerZoneProfile(athlete_id=athlete_id, zones=[{"name": "Рабочая", "minimum": 200, "maximum": None}]),
                HeartRateZoneProfile(athlete_id=athlete_id, zones=[{"name": "Рабочая", "minimum": 130, "maximum": 160}]),
            ])
            db.commit()
        importer = WorkoutImportService(engine, settings)
        importer.import_fit(athlete_id, cycling_fit(start=start), filename="power.fit")
        importer.import_fit(athlete_id, cycling_fit(start=start + 86400, power=None), filename="no-power.fit")
    finally:
        engine.dispose()


def main() -> None:
    origin = os.environ["CTM_E2E_ORIGIN"]
    address = urlsplit(origin)
    directory = Path(os.environ["CTM_E2E_DIRECTORY"])
    password = os.environ["CTM_E2E_PASSWORD"]
    pilot = os.environ.get("CTM_E2E_G5") == "1"
    if pilot:
        settings = seed_training_pilot(directory / "pilot", password, origin).model_copy(update={"serve_frontend": True})
        training_routes.time = wellness.time = AS_OF.timestamp
    else:
        database = directory / "test.sqlite3"
        os.environ["CTM_DATABASE_PATH"] = str(database)
        command.upgrade(Config(BACKEND / "alembic.ini"), "head")
        settings = Settings(
            database_path=database, fit_path=database.parent / "fit", allowed_origins=[origin], serve_frontend=True,
            secure_cookies=False, onelap_gateway="", onelap_keys=[],
        )
        bootstrap_coach(settings, "coach@example.com", "Главный тренер", password)
        seed_stage_b(settings, password)
        seed_analytics(settings, password)
    if static_root := os.environ.get("CTM_E2E_STATIC_ROOT"):
        application.ROOT = Path(static_root)
    uvicorn.run(application.create_app(settings, start_onelap_worker=not pilot), host=address.hostname, port=address.port)


if __name__ == "__main__":
    main()
