"""Deterministic G5 analysis fixtures for a synthetic team of 20 athletes and 3 coaches."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.athlete_models import AthleteProfile, FtpRecord, WellnessEntry, Workout
from app.athlete_schemas import WorkoutSummary
from app.config import Settings
from app.database import make_engine
from app.models import User, now
from app.security import passwords
from app.training_intelligence.power import POWER_PROJECTION_VERSION
from app.workout_import import WorkoutImportService
from app.workout_models import WorkoutEffort, WorkoutMetrics
from fit_fixtures import cycling_fit

AS_OF = datetime(2026, 4, 16, 12, tzinfo=UTC)
HISTORY_DAYS = 49
ZONES = ("Europe/Moscow", "Asia/Yekaterinburg", "America/New_York", "UTC")
PROTOCOL = "pilot-60s"
CONTROL_DAYS = (12, 22, 32, 45, 47)


@dataclass(frozen=True)
class Case:
    key: str
    normal: float
    recent: float
    expected: tuple[str, ...] = ()
    worsening: tuple[int, ...] = ()
    sleep_only: tuple[int, ...] = ()
    overload: tuple[int, ...] = ()
    missing_fit: tuple[int, ...] = ()
    missing_diary: tuple[int, ...] = ()
    rest: bool = False
    history_starts: int = 0
    recent_power: tuple[int, int] | None = None
    other_protocol: bool = False
    rest_conflict: int | None = None
    example_fit: int | None = None


# Fixed oracle. Items combine rule letter and expected level (i=informational, m=moderate, h=high).
# All omitted signals are forbidden for that scenario at the reference date.
CASES = (
    Case("high-stable", 75, 100),
    Case("high-worsening", 75, 100, ("A:m", "B:m"), worsening=(46, 47), example_fit=46),
    Case("normal-bad-sleep", 105, 105, sleep_only=(45, 46, 47)),
    Case("heavy-streak", 80, 120, ("A:h", "B:m"), worsening=(46, 47, 48)),
    Case("recovery-after-drop", 100, 100, ("F:i",), worsening=(42, 43), overload=(40, 41)),
    Case("growth-improved", 80, 110, ("D:i",), recent_power=(220, 224)),
    Case("growth-declined", 90, 120, ("C:m",), recent_power=(180, 184)),
    Case("missing-fit", 95, 95, ("E:i",), missing_fit=(46, 47, 48)),
    Case("missing-diary", 90, 130, ("E:i",), missing_diary=tuple(range(39, 49))),
    Case("confirmed-rest", 100, 100, rest=True),
    Case("short-history", 95, 95, ("E:i",), history_starts=45),
    Case("same-dose-high", 75, 100, ("A:m", "B:m"), worsening=(46, 47)),
    Case("same-dose-normal", 112, 100, worsening=(46, 47)),
    Case("growth-at-threshold", 100, 115, ("B:m",), worsening=(46, 47)),
    Case("below-fatigue-threshold", 100, 119.9, worsening=(42, 43)),
    Case("fatigue-at-threshold", 100, 120, ("A:m", "B:m"), worsening=(47, 48)),
    Case("incomparable-protocol", 80, 110, recent_power=(180, 184), other_protocol=True),
    Case("decline-at-threshold", 100, 100, ("C:m",), recent_power=(190, 190)),
    Case("below-decline-threshold", 100, 100, recent_power=(191, 191)),
    Case("rest-conflicts-with-fit", 100, 105, ("E:i",), worsening=(46, 47), rest_conflict=48),
)


def pilot_day(index: int) -> date:
    return AS_OF.date() - timedelta(days=HISTORY_DAYS - index)


def athlete_email(case: Case) -> str:
    return f"{case.key}@example.com"


def fixture_sleep(athlete_number: int) -> int:
    return 410 + (athlete_number * 11) % 95


def workout_time(day: date, *, control: bool = False) -> int:
    return int(datetime.combine(day, time(13 if control else 12), UTC).timestamp())


def add_projected_workout(db: Session, athlete_id: int, timezone: str, day: date, tss: float):
    # Deliberately inject known TSS to keep the A–F oracle independent of FIT/NP.
    # Original FIT artifacts are imported separately by the regular importer.
    summary = dict.fromkeys(WorkoutSummary.model_fields)
    summary.update(elapsed_seconds=3600, sports=["cycling"], gps=False, sample_count=0)
    workout = Workout(athlete_id=athlete_id, started_at=workout_time(day), timer_seconds=3600, summary=summary)
    db.add(workout)
    db.flush()
    db.add(WorkoutMetrics(
        workout_id=workout.id, version="g5-synthetic-projection",
        snapshot={"local_date": day.isoformat(), "timezone": timezone, "ftp_watts": None,
                  "ftp_effective_from": None, "power_zones": [], "heart_rate_zones": [], "algorithm": {}},
        result={"tss": tss, "np": None, "if_value": None, "power_zones": None, "heart_rate_zones": None,
                "power_distribution": None, "reasons": {},
                "quality": {"timing_source": "synthetic", "expected_seconds": None, "covered_seconds": {},
                            "np_windows": 0, "possible_np_windows": 0, "missing_channels": ["power"]}},
        metadata_json={},
    ))


def seed_training_pilot(directory: Path, password: str, origin: str = "https://testserver") -> Settings:
    """Create an isolated database. The directory must not already exist."""
    directory.mkdir(parents=True)
    settings = Settings(
        _env_file=None, database_path=directory / "team.sqlite3", fit_path=directory / "fit",
        allowed_origins=[origin], secure_cookies=origin.startswith("https:"),
        serve_frontend=False, onelap_gateway="", onelap_keys=[],
    )
    config = Config(Path(__file__).resolve().parents[1] / "alembic.ini")
    config.attributes["settings"] = settings
    command.upgrade(config, "head")
    engine = make_engine(settings)
    importer = WorkoutImportService(engine, settings)
    imports = []
    try:
        with Session(engine) as db:
            encoded = passwords.hash(password)
            for number in range(1, 4):
                db.add(User(email=f"pilot-coach{number}@example.com", name=f"Тестовый тренер {number}",
                            role="coach", password_hash=encoded, activated_at=now()))
            for number, case in enumerate(CASES, 1):
                user = User(email=athlete_email(case), name=f"{number:02d} · {case.key}", role="athlete",
                            password_hash=encoded, activated_at=now())
                db.add(user)
                db.flush()
                zone = ZONES[(number - 1) % len(ZONES)]
                db.add(AthleteProfile(athlete_id=user.id, timezone=zone, team_group="Синтетический пилот"))
                db.flush()
                db.add_all([
                    FtpRecord(athlete_id=user.id, effective_from=pilot_day(0), watts=250),
                    FtpRecord(athlete_id=user.id, effective_from=pilot_day(38), watts=300),
                ])
                control = dict(zip(CONTROL_DAYS, (190, 200, 210, *case.recent_power), strict=True)) if case.recent_power else {}
                for index in range(case.history_starts, HISTORY_DAYS):
                    day = pilot_day(index)
                    rest = case.rest and index % 7 in {1, 4}
                    no_fit = index in case.missing_fit
                    day_status = None if no_fit else "rest" if rest or index == case.rest_conflict else "all_workouts_recorded"
                    if index not in case.missing_diary:
                        worse = index in case.worsening
                        bad_sleep = worse or index in case.sleep_only
                        sleep_quality = 3 + number % 3
                        fatigue = 1 + number % 2
                        recovery = 3 + (number + 1) % 3
                        db.add(WellnessEntry(
                            athlete_id=user.id, entry_date=day, timezone=zone, training_day_status=day_status,
                            sleep_minutes=fixture_sleep(number) - (60 if bad_sleep else 0),
                            sleep_quality=sleep_quality - int(bad_sleep), general_fatigue=fatigue + int(worse),
                            leg_fatigue=2, motivation=4, mood=3 + number % 2, muscle_soreness=2,
                            recovery=recovery - int(worse), illness_symptoms=False,
                            comment="G5_PRIVATE_SYNTHETIC_DIARY",
                        ))
                    if rest or no_fit:
                        continue
                    total = 250 if index in case.overload else case.recent if index >= 42 else case.normal
                    if index in control or index == case.example_fit:
                        watts = control.get(index, 200)
                        protocol = (("other-60s" if case.other_protocol and index == CONTROL_DAYS[-1] else PROTOCOL)
                                    if index in control else None)
                        imports.append((user.id, day, watts, protocol))
                        # Closed-form TSS of 120 s of constant power from the known FTP schedule.
                        total -= (120 / 3600) * (watts / (250 if index < 38 else 300)) ** 2 * 100
                    add_projected_workout(db, user.id, zone, day, total)
            db.commit()
        for athlete_id, day, watts, protocol in imports:
            result = importer.import_fit(
                athlete_id, cycling_fit(start=workout_time(day, control=True), power=watts),
                filename="pilot-synthetic.fit",
            )
            if protocol is not None:
                with Session(engine) as db:
                    workout = db.get(Workout, result.workout_id)
                    db.add(WorkoutEffort(
                        workout_id=workout.id, protocol=protocol, start_seconds=10, duration_seconds=60,
                        file_sha256=workout.file_sha256, projection_version=POWER_PROJECTION_VERSION, mean_watts=watts,
                    ))
                    db.commit()
    finally:
        engine.dispose()
    return settings
