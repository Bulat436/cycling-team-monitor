"""Shared FIT import and reprocessing used by HTTP and future providers."""

import json
import os
import re
import tempfile
import unicodedata
import zlib
from dataclasses import dataclass
from hashlib import file_digest, sha256

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from .athlete_models import AthleteProfile, FtpRecord, HeartRateZoneProfile, PowerZoneProfile, Workout
from .athletes import local_date
from .config import Settings
from .fit_parser import Activity, FitProblem, parse_fit
from .models import AuthSession, User, now
from .security import RequestActor, audit
from .workout_metrics import PARAMETERS, VERSION, calculate
from .workout_models import WorkoutFile, WorkoutImport, WorkoutMetrics, WorkoutSampleBlock, WorkoutSource
from .workout_report import report_workout_key

BLOCK_SIZE = 512
DUPLICATE_SECONDS = 5


@dataclass(frozen=True)
class ImportResult:
    workout_id: int
    status: str
    potential_duplicates: list[int]


def safe_filename(value: str) -> str:
    leaf = value.replace(chr(92), "/").split("/")[-1].strip()
    leaf = "".join(character for character in leaf if not unicodedata.category(character).startswith("C")).strip()
    return leaf[:176] + ".fit" if len(leaf) > 180 and leaf.lower().endswith(".fit") else leaf[:180]


def pack_samples(samples: list[dict]) -> list[bytes]:
    return [
        zlib.compress(json.dumps(samples[start:start + BLOCK_SIZE], ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode())
        for start in range(0, len(samples), BLOCK_SIZE)
    ]


def verify_actor(db: Session, actor: RequestActor):
    user = db.get(User, actor.user_id)
    session = db.get(AuthSession, actor.session_hash)
    if not user or not user.active or not user.password_hash or not session or session.user_id != user.id or session.expires_at <= now():
        raise FitProblem("authentication_required", 401)
    if user.role != actor.role:
        raise FitProblem("role_denied", 403)


def similar_workouts(workout: Workout, candidate: Workout) -> bool:
    duration, other_duration = workout.timer_seconds, candidate.timer_seconds
    if duration is None or other_duration is None or abs(duration - other_duration) > 10:
        return False
    distance = (workout.summary or {}).get("distance_m")
    other_distance = (candidate.summary or {}).get("distance_m")
    return (
        distance is None or other_distance is None
        or abs(distance - other_distance) <= max(100, distance * 0.02)
    )


class WorkoutImportService:
    def __init__(self, engine, settings: Settings):
        self.engine = engine
        self.root = settings.fit_directory
        self.max_bytes = settings.fit_max_bytes

    def original_path(self, athlete_id: int, digest: str):
        if athlete_id < 1 or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise FitProblem("fit_original_missing", 404)
        directory = self.root / str(athlete_id)
        path = directory / (digest + ".fit")
        if path.resolve() != path:
            raise FitProblem("fit_storage_error", 503)
        return path

    def _save_original(self, athlete_id, digest, payload):
        path = self.original_path(athlete_id, digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            with path.open("rb") as existing:
                if file_digest(existing, "sha256").hexdigest() == digest:
                    return
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".upload-", delete=False) as target:
                temporary_path = target.name
                target.write(payload)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None and os.path.exists(temporary_path):
                os.unlink(temporary_path)

    def _profile(self, db, athlete_id, actor):
        if actor:
            verify_actor(db, actor)
            if actor.role == "athlete" and actor.user_id != athlete_id:
                raise FitProblem("athlete_missing", 404)
        profile = db.get(AthleteProfile, athlete_id)
        user = db.get(User, athlete_id)
        if not profile or not user or user.role != "athlete":
            raise FitProblem("athlete_missing", 404)
        if not user.active and (actor is None or actor.role != "coach"):
            raise FitProblem("athlete_inactive", 403)
        return profile

    @staticmethod
    def _snapshot(db, profile, started_at):
        day = local_date(profile.timezone, started_at)
        ftp = db.scalar(
            select(FtpRecord).where(FtpRecord.athlete_id == profile.athlete_id, FtpRecord.effective_from <= day)
            .order_by(FtpRecord.effective_from.desc()).limit(1)
        )
        power = db.get(PowerZoneProfile, profile.athlete_id)
        heart_rate = db.get(HeartRateZoneProfile, profile.athlete_id)
        return {
            "timezone": profile.timezone, "local_date": day.isoformat(),
            "ftp_watts": ftp.watts if ftp else None,
            "ftp_effective_from": ftp.effective_from.isoformat() if ftp else None,
            "power_zones": power.zones if power else [],
            "heart_rate_zones": heart_rate.zones if heart_rate else [],
        }

    @staticmethod
    def _project(db, workout, activity, blocks, snapshot):
        calculation = calculate(activity, snapshot)
        workout.started_at = activity.started_at
        workout.timer_seconds = calculation.timer_seconds
        workout.summary = calculation.summary
        db.execute(delete(WorkoutSampleBlock).where(WorkoutSampleBlock.workout_id == workout.id))
        db.add_all([
            WorkoutSampleBlock(workout_id=workout.id, block_index=index, payload=payload)
            for index, payload in enumerate(blocks)
        ])
        metrics = db.get(WorkoutMetrics, workout.id)
        if metrics is None:
            metrics = WorkoutMetrics(workout_id=workout.id)
            db.add(metrics)
        metrics.version = VERSION
        metrics.snapshot = {**snapshot, "algorithm": PARAMETERS}
        metrics.result = calculation.metrics
        metrics.metadata_json = {**activity.metadata, "timer_events": activity.events}

    def log_failure(self, athlete_id, filename, source, code, actor=None, digest=None, guard=None):
        with Session(self.engine) as db:
            db.execute(text("BEGIN IMMEDIATE"))
            self._profile(db, athlete_id, actor)
            if guard is not None:
                guard(db)
            db.add(WorkoutImport(
                athlete_id=athlete_id, source=source, filename=safe_filename(filename),
                file_sha256=digest, status="failed", error_code=code, candidates=[],
            ))
            db.commit()

    def import_fit(
        self, athlete_id: int, payload: bytes | bytearray, *, filename: str,
        source: str = "manual", external_id: str | None = None, actor: RequestActor | None = None,
        guard=None,
    ) -> ImportResult:
        if source not in {"manual", "onelapfit"} or (external_id is not None and not 1 <= len(external_id) <= 256):
            raise ValueError("Unsupported import source or invalid external ID")
        if actor and actor.role != "athlete":
            raise FitProblem("role_denied", 403)
        filename = safe_filename(filename)
        digest = None
        try:
            if not filename.lower().endswith(".fit"):
                raise FitProblem("fit_extension")
            if not payload:
                raise FitProblem("fit_empty")
            if len(payload) > self.max_bytes:
                raise FitProblem("fit_too_large", 413)
            digest = sha256(payload).hexdigest()
            activity = parse_fit(payload, max_bytes=self.max_bytes)
            blocks = pack_samples(activity.samples)
            return self._commit_import(athlete_id, payload, filename, source, external_id, actor, digest, activity, blocks, guard)
        except OSError as exc:
            self.log_failure(athlete_id, filename, source, "fit_storage_error", actor, digest, guard)
            raise FitProblem("fit_storage_error", 503) from exc
        except FitProblem as exc:
            if exc.code not in {"authentication_required", "role_denied", "athlete_missing", "athlete_inactive"}:
                self.log_failure(athlete_id, filename, source, exc.code, actor, digest, guard)
            raise

    def _commit_import(self, athlete_id, payload, filename, source, external_id, actor, digest, activity, blocks, guard):
        key = "external:" + external_id if external_id is not None else "sha256:" + digest
        with Session(self.engine) as db:
            db.execute(text("BEGIN IMMEDIATE"))
            profile = self._profile(db, athlete_id, actor)
            if guard is not None:
                guard(db)
            exact = db.get(WorkoutFile, (athlete_id, digest))
            matching = exact or db.scalar(select(WorkoutFile).where(
                WorkoutFile.athlete_id == athlete_id, WorkoutFile.fit_sha256 == activity.fit_hash,
            ).limit(1))
            origin = db.get(WorkoutSource, (athlete_id, source, key))
            if matching and origin and matching.workout_id != origin.workout_id:
                raise FitProblem("fit_source_conflict", 409)

            known_id = matching.workout_id if matching else origin.workout_id if origin else None
            workout = db.get(Workout, known_id) if known_id is not None else None
            if workout is None and (reported := db.get(WorkoutSource, (
                athlete_id, "report", report_workout_key(activity.started_at),
            ))) is not None:
                candidate = db.get(Workout, reported.workout_id)
                if candidate.file_sha256 is None:
                    workout = candidate
            candidates = []
            if workout is None:
                workout = Workout(athlete_id=athlete_id, started_at=activity.started_at)
                db.add(workout)
                db.flush()
                status = "imported"
            else:
                status = "duplicate" if matching else "updated"

            if status != "duplicate":
                previous = db.get(WorkoutMetrics, workout.id)
                snapshot = previous.snapshot if previous else self._snapshot(db, profile, activity.started_at)
                if previous and local_date(snapshot["timezone"], activity.started_at).isoformat() != snapshot["local_date"]:
                    snapshot = self._snapshot(db, profile, activity.started_at)
                self._project(db, workout, activity, blocks, snapshot)
                workout.file_sha256 = digest

            if status == "imported":
                candidates = self._possible_duplicates(db, workout)
            self._save_original(athlete_id, digest, payload)
            if not exact:
                db.add(WorkoutFile(
                    athlete_id=athlete_id, workout_id=workout.id, sha256=digest, fit_sha256=activity.fit_hash,
                    filename=filename, byte_count=len(payload), container=activity.container,
                ))
            if origin is None:
                db.add(WorkoutSource(
                    athlete_id=athlete_id, source=source, source_key=key, workout_id=workout.id,
                    external_id=external_id, file_sha256=digest,
                ))
            elif status == "updated":
                origin.file_sha256 = digest
            db.add(WorkoutImport(
                athlete_id=athlete_id, workout_id=workout.id, source=source, filename=filename,
                file_sha256=digest, status=status, candidates=candidates,
            ))
            result = ImportResult(workout.id, status, candidates)
            db.commit()
            return result

    @staticmethod
    def _possible_duplicates(db, workout):
        rows = db.scalars(select(Workout).where(
            Workout.athlete_id == workout.athlete_id, Workout.id != workout.id,
            Workout.started_at.between(workout.started_at - DUPLICATE_SECONDS, workout.started_at + DUPLICATE_SECONDS),
        ).order_by(Workout.started_at, Workout.id).limit(100))
        return [candidate.id for candidate in rows if similar_workouts(workout, candidate)]

    def reprocess(self, workout_id: int, actor: RequestActor, *, refresh_parameters: bool = False):
        if actor.role != "coach":
            raise FitProblem("role_denied", 403)
        with Session(self.engine) as db:
            verify_actor(db, actor)
            workout = db.get(Workout, workout_id)
            if workout is None:
                raise FitProblem("workout_missing", 404)
            athlete_id, digest = workout.athlete_id, workout.file_sha256
            if digest is None:
                raise FitProblem("fit_original_missing", 404)
        path = self.original_path(athlete_id, digest)
        try:
            with path.open("rb") as original:
                payload = original.read(self.max_bytes + 1)
        except FileNotFoundError as exc:
            raise FitProblem("fit_original_missing", 404) from exc
        except OSError as exc:
            raise FitProblem("fit_storage_error", 503) from exc
        if len(payload) > self.max_bytes:
            raise FitProblem("fit_too_large", 413)
        if sha256(payload).hexdigest() != digest:
            raise FitProblem("fit_original_changed", 409)
        activity = parse_fit(payload, max_bytes=self.max_bytes)
        blocks = pack_samples(activity.samples)

        with Session(self.engine) as db:
            db.execute(text("BEGIN IMMEDIATE"))
            profile = self._profile(db, athlete_id, actor)
            workout = db.get(Workout, workout_id)
            if workout is None or workout.file_sha256 != digest:
                raise FitProblem("fit_original_changed", 409)
            old = db.get(WorkoutMetrics, workout_id)
            snapshot = self._snapshot(db, profile, activity.started_at) if refresh_parameters or old is None else old.snapshot
            self._project(db, workout, activity, blocks, snapshot)
            audit(db, "workout_parameters_refreshed" if refresh_parameters else "workout_reprocessed", actor.user_id, athlete_id)
            db.commit()
