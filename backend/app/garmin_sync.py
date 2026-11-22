"""Import source observations into a database reserved for the local owner."""

import json
from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import date, datetime
from hashlib import sha256
from time import sleep, time
from zoneinfo import ZoneInfo

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from .garmin_models import GarminPersonalConnection, GarminSleepObservation, GarminSyncRun
from .garmin_provider import GarminClient, GarminError, SleepBatch, SleepObservation
from .models import User


def previous_observation(db: Session, observation: SleepObservation, fallback: str):
    model = GarminSleepObservation
    latest = select(model).order_by(model.id.desc()).limit(1)
    if observation.external_id is not None:
        previous = db.scalar(latest.where(model.external_id == observation.external_id))
        if previous is not None:
            return previous
        if db.scalar(latest.where(model.source_key == fallback, model.external_id.is_not(None))) is not None:
            return None
    else:
        keys = db.scalars(select(model.source_key).where(
            model.garmin_date == observation.garmin_date, model.start_ms == observation.start_ms,
            model.end_ms == observation.end_ms,
        ).distinct().limit(2)).all()
        if len(keys) == 1:
            return db.scalar(latest.where(model.source_key == keys[0]))
    return db.scalar(latest.where(model.source_key == fallback))


def store_observation(db: Session, connection: GarminPersonalConnection, observation: SleepObservation, received_at: float):
    fallback = f"window:{observation.garmin_date}:{observation.start_ms}:{observation.end_ms}"
    previous = previous_observation(db, observation, fallback)
    source_key = fallback if observation.external_id is None else f"id:{observation.external_id}"
    zone = connection.timezone
    if previous is not None:
        source_key = previous.source_key
        zone = previous.timezone
    fields = asdict(observation)
    digest = sha256(json.dumps(fields, default=str, sort_keys=True).encode()).hexdigest()
    duplicate = db.scalar(select(GarminSleepObservation.id).where(
        GarminSleepObservation.source_key == source_key, GarminSleepObservation.content_hash == digest,
    ))
    if duplicate is not None:
        return False
    # Keep the original local day linked to a record through provider corrections.
    sleep_date = previous.sleep_date if previous else datetime.fromtimestamp(observation.end_ms / 1000, ZoneInfo(zone)).date()
    db.add(GarminSleepObservation(
        connection_id=connection.id, source_key=source_key, content_hash=digest,
        sleep_date=sleep_date, timezone=zone, received_at=received_at, **fields,
    ))
    return True


class GarminPersonalStore:
    def __init__(self, engine, *, clock=time, wait=sleep):
        self.engine = engine
        self.clock = clock
        self.wait = wait

    @contextmanager
    def transaction(self):
        with Session(self.engine, expire_on_commit=False) as db, db.begin():
            db.execute(text("BEGIN IMMEDIATE"))
            connection = db.get(GarminPersonalConnection, 1)
            if connection is None or db.scalar(select(User.id).limit(1)) is not None:
                raise GarminError("personal_database_required")
            yield db, connection

    def status(self):
        with self.transaction() as (db, connection):
            last = db.scalar(select(GarminSyncRun).order_by(GarminSyncRun.id.desc()).limit(1))
            return connection, last

    def configure(self, history_days: int | None, request_interval: int | None):
        with self.transaction() as (_, connection):
            connection.history_days = history_days
            connection.request_interval_seconds = request_interval

    def _reserve_request(self):
        while True:
            with self.transaction() as (_, connection):
                now = self.clock()
                if connection.blocked_until is not None and now < connection.blocked_until:
                    raise GarminError("rate_limited", connection.blocked_until)
                if connection.request_interval_seconds is None:
                    raise GarminError("not_connected")
                if connection.last_request_at is None:
                    delay = 0.0
                else:
                    delay = connection.last_request_at + connection.request_interval_seconds - now
                if delay <= 0:
                    connection.last_request_at = now
                    return
                if delay > connection.request_interval_seconds:
                    raise GarminError("request_deferred", now + delay)
            self.wait(delay)

    def _block_requests(self, retry_at: float):
        with self.transaction() as (_, connection):
            connection.blocked_until = max(connection.blocked_until or 0, retry_at)

    def _start(self, start: date, end: date) -> int:
        with self.transaction() as (db, connection):
            if connection.history_days is None or connection.request_interval_seconds is None:
                raise GarminError("not_connected")
            now = self.clock()
            today = datetime.fromtimestamp(now, ZoneInfo(connection.timezone)).date()
            if start > end or end > today or (today - start).days >= connection.history_days:
                raise GarminError("invalid_range")
            db.execute(update(GarminSyncRun).where(GarminSyncRun.status == "running").values(
                status="interrupted", ended_at=now,
            ))
            run = GarminSyncRun(connection_id=connection.id, start_date=start, end_date=end, started_at=now, status="running")
            db.add(run)
            db.flush()
            return run.id

    def _save_batch(self, run_id: int, batch: SleepBatch):
        with self.transaction() as (db, connection):
            if len(batch.account_ids) > 1 or (connection.account_pk is not None and batch.account_ids - {connection.account_pk}):
                raise GarminError("account_mismatch")
            if connection.account_pk is None and batch.observations:
                connection.account_pk = next(iter(batch.account_ids))
            run = db.get(GarminSyncRun, run_id)
            received_at = self.clock()
            for observation in batch.observations:
                if store_observation(db, connection, observation, received_at):
                    run.inserted += 1
                else:
                    run.unchanged += 1
            run.skipped += batch.skipped

    def _finish(self, run_id: int, error: str | None) -> GarminSyncRun:
        with self.transaction() as (db, connection):
            run = db.get(GarminSyncRun, run_id)
            run.ended_at = self.clock()
            if error is not None:
                run.status = error
            elif run.skipped:
                run.status = "partial"
            else:
                run.status = "success"
                connection.last_success_at = run.ended_at
            return run

    def sync(self, start: date, end: date, api_key: str, *, transport=None) -> GarminSyncRun:
        run_id = self._start(start, end)
        try:
            with closing(GarminClient(
                api_key, reserve=self._reserve_request, block=self._block_requests,
                transport=transport, clock=self.clock, wait=self.wait,
            )) as client:
                for batch in client.sleep_batches(start, end):
                    self._save_batch(run_id, batch)
        except GarminError as error:
            self._finish(run_id, error.code)
            raise
        return self._finish(run_id, None)
