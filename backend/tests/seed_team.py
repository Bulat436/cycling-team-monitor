"""Build an isolated synthetic 20-athlete, 3-coach team."""
import argparse
from datetime import timedelta
from getpass import getpass
from pathlib import Path
import sys
from time import time

from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.athlete_models import (
    AthleteProfile, FtpRecord, HeartRateZoneProfile, PowerZoneProfile, RpeEntry, WeightRecord, WellnessEntry,
)
from app.athletes import local_date
from app.config import Settings, private_storage_path
from app.database import make_engine
from app.models import User, now
from app.onelap_models import OnelapConnection, SyncError, SyncRun
from app.security import passwords
from app.workout_import import WorkoutImportService
from fit_fixtures import cycling_fit

DEMO_DOMAIN = "example.com"


def seed_team(directory: Path, password: str) -> Settings:
    if not 12 <= len(password) <= 128:
        raise ValueError("Use a 12–128 character password unique to the test team.")
    directory = private_storage_path(directory.absolute())
    directory.mkdir(parents=True)
    settings = Settings(
        _env_file=None, database_path=directory / "team.sqlite3", fit_path=directory / "fit",
        onelap_gateway="", onelap_keys=[], secure_cookies=False, serve_frontend=True,
    )
    config = Config(BACKEND / "alembic.ini")
    config.attributes["settings"] = settings
    command.upgrade(config, "head")
    engine = make_engine(settings)
    importer = WorkoutImportService(engine, settings)
    stamp = int(time())
    rides = []
    try:
        with Session(engine) as db:
            encoded = passwords.hash(password)
            for number in range(1, 4):
                db.add(User(email=f"coach{number:02}@{DEMO_DOMAIN}", name=f"Тестовый тренер {number}",
                            role="coach", password_hash=encoded, activated_at=now()))
            for number in range(1, 21):
                athlete = User(email=f"athlete{number:02}@{DEMO_DOMAIN}", name=f"Тестовый спортсмен {number:02}",
                               role="athlete", password_hash=encoded, activated_at=now())
                db.add(athlete)
                db.flush()
                zone = "Asia/Yekaterinburg" if number % 4 == 0 else "Europe/Moscow"
                today = local_date(zone, stamp)
                profile = AthleteProfile(athlete_id=athlete.id, timezone=zone, height_cm=170 + number,
                                         team_group="Шоссе" if number % 2 else "Трек")
                db.add(profile)
                db.flush()
                db.add_all([
                    FtpRecord(athlete_id=athlete.id, effective_from=today - timedelta(days=28), watts=220 + number * 2),
                    FtpRecord(athlete_id=athlete.id, effective_from=today - timedelta(days=7), watts=225 + number * 2),
                    WeightRecord(athlete_id=athlete.id, measured_on=today - timedelta(days=4), kg=58 + number),
                    PowerZoneProfile(athlete_id=athlete.id, zones=[
                        {"name": "Лёгкая", "minimum": 0, "maximum": 180},
                        {"name": "Рабочая", "minimum": 180, "maximum": None},
                    ]),
                    HeartRateZoneProfile(athlete_id=athlete.id, zones=[
                        {"name": "Лёгкая", "minimum": 0, "maximum": 140},
                        {"name": "Рабочая", "minimum": 140, "maximum": None},
                    ]),
                ])
                for offset in (0, 1, 3, 8, 16):
                    if offset == 0 and number % 5 == 0:
                        continue
                    db.add(WellnessEntry(
                        athlete_id=athlete.id, entry_date=today - timedelta(days=offset), timezone=zone,
                        sleep_quality=2 + number % 4, general_fatigue=5 if number % 7 == 0 else 2,
                        leg_fatigue=4 if number % 6 == 0 else 2, motivation=1 if number % 9 == 0 else 4,
                        mood=4, muscle_soreness=5 if number % 8 == 0 else 2,
                        illness_symptoms=offset == 0 and number % 11 == 0,
                    ))
                # Historical diagnostics describe simulated runs. No credentials or enabled gateway are present.
                if number % 4:
                    status, diagnostic = {
                        1: ("synced", None), 2: ("temporary_error", "network_error"),
                        3: ("reauthorization", "reauthorization"),
                    }[number % 4]
                    row = OnelapConnection(athlete_id=athlete.id, status=status, history_days=90, diagnostic=diagnostic)
                    db.add(row)
                    db.flush()
                    run = SyncRun(athlete_id=athlete.id, generation=row.generation, trigger="manual",
                                  status="failed" if diagnostic else "complete", diagnostic=diagnostic,
                                  started_at=stamp - 600, ended_at=stamp - 599)
                    db.add(run)
                    if diagnostic:
                        db.flush()
                        db.add(SyncError(run_id=run.id, code=diagnostic))
                rides.append((athlete.id, number))
            db.commit()

        with Session(engine) as db:
            for athlete_id, number in rides:
                offsets = (20, 24) if number == 20 else (1, 4, 10, 24)
                for offset in offsets:
                    ride = cycling_fit(
                        start=stamp - offset * 86400 - number * 60, elapsed=240 + number * 3,
                        power=None if number % 6 == 0 else 175 + (number * 5 + offset) % 95,
                        distance=1200 + number * 40,
                    )
                    imported = importer.import_fit(athlete_id, ride, filename=f"demo-{offset}.fit")
                    if (number + offset) % 5:
                        db.add(RpeEntry(workout_id=imported.workout_id, score=2 + (number + offset) % 8))
            db.commit()
    finally:
        engine.dispose()
    return settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    if not sys.stdin.isatty():
        parser.error("Run in a terminal so the test password can be entered without echo.")
    password = getpass("Пароль для тестовой команды: ")
    if password != getpass("Повторите пароль: "):
        parser.error("Пароли не совпадают.")
    try:
        seed_team(args.directory, password)
    except (FileExistsError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
    print("Создана отдельная тестовая команда. Используйте coach01@example.com или athlete01@example.com.")


if __name__ == "__main__":
    main()
