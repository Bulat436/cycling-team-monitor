"""Run the built application on a temporary SQLite database for Playwright."""

import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.athlete_models import AthleteProfile, WellnessEntry, Workout
from app.athletes import local_date
from app.cli import bootstrap_coach
from app.config import Settings
from app.database import make_engine
from app.main import create_app
from app.models import User, now
from app.security import passwords


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


def main() -> None:
    origin = os.environ["CTM_E2E_ORIGIN"]
    address = urlsplit(origin)
    directory = os.environ["CTM_E2E_DIRECTORY"]
    database = Path(directory) / "test.sqlite3"
    os.environ["CTM_DATABASE_PATH"] = str(database)
    command.upgrade(Config(BACKEND / "alembic.ini"), "head")
    settings = Settings(
        database_path=database, fit_path=database.parent / "fit", allowed_origins=[origin], serve_frontend=True, secure_cookies=False, onelap_gateway="", onelap_keys=[],
    )
    password = os.environ["CTM_E2E_PASSWORD"]
    bootstrap_coach(settings, "coach@example.com", "Главный тренер", password)
    seed_stage_b(settings, password)
    uvicorn.run(create_app(settings), host=address.hostname, port=address.port)


if __name__ == "__main__":
    main()
