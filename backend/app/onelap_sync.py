from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from time import time

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from .athlete_models import AthleteProfile
from .fit_parser import FitProblem
from .models import User
from .onelap_models import OnelapConnection, OnelapRevision, SyncError, SyncRun
from .onelap_provider import ProviderError
from .security import audit
from .workout_import import verify_actor

ACTIVE_RUNS = ("queued", "running")
MAX_PAGES = 100
OVERLAP_SECONDS = 3 * 86400
CHECK_TIMEOUT = 120
RETRY_LIMIT_SECONDS = 6 * 3600


def digest(*parts: str):
    return sha256("\0".join(parts).encode()).hexdigest()


def cancel_connection(db: Session, connection: OnelapConnection):
    connection.generation += 1
    connection.secret = None
    connection.account_hash = None
    connection.status = "not_connected"
    connection.watermark = None
    connection.last_success_at = None
    connection.next_sync_at = None
    connection.failures = 0
    connection.diagnostic = None
    db.execute(delete(OnelapRevision).where(OnelapRevision.athlete_id == connection.athlete_id))
    db.execute(update(SyncRun).where(
        SyncRun.athlete_id == connection.athlete_id, SyncRun.status.in_(ACTIVE_RUNS),
    ).values(status="cancelled", ended_at=time(), diagnostic="connection_changed"))


def problem_status(code: str):
    if code in {"invalid_credentials", "reauthorization", "account_mismatch"}:
        return "reauthorization"
    if code == "interaction_required":
        return "interaction_required"
    if code in {"provider_denied", "provider_unavailable", "key_unavailable"}:
        return "unavailable"
    return "temporary_error"


@dataclass(frozen=True)
class Claim:
    id: int
    athlete_id: int
    generation: int
    trigger: str
    secret: str
    history_days: int
    watermark: int | None


class OnelapSyncService:
    def __init__(self, engine, provider, vault, importer, settings, stopped):
        self.engine = engine
        self.provider = provider
        self.vault = vault
        self.importer = importer
        self.stopped = stopped
        self.interval = settings.onelap_interval_seconds
        self.default_history = settings.onelap_history_days
        self.validated = settings.onelap_validated

    @property
    def available(self):
        return self.provider is not None and self.vault.cipher is not None

    def require_available(self):
        if not self.available:
            raise ProviderError("provider_unavailable" if self.provider is None else "key_unavailable")

    @contextmanager
    def transaction(self):
        with Session(self.engine, expire_on_commit=False) as db:
            db.execute(text("BEGIN IMMEDIATE"))
            yield db
            db.commit()

    def connect(self, actor, login, password, history_days):
        self.require_available()
        athlete_id = actor.user_id
        with self.transaction() as db:
            verify_actor(db, actor)
            if actor.role != "athlete" or db.get(AthleteProfile, athlete_id) is None:
                raise ProviderError("role_denied")
            connection = db.get(OnelapConnection, athlete_id)
            current = time()
            if connection is None:
                connection = OnelapConnection(athlete_id=athlete_id, history_days=history_days, generation=1)
                db.add(connection)
                generation = 1
            else:
                if connection.status == "syncing" or (
                    connection.status == "checking" and current - connection.updated_at < CHECK_TIMEOUT
                ):
                    raise ProviderError("sync_busy")
                if connection.secret:
                    raise ProviderError("already_connected")
                retry_at = max(
                    (connection.connect_attempt_at or 0) + 60,
                    (connection.next_sync_at or 0) if connection.failures else 0,
                )
                if retry_at > current:
                    raise ProviderError("sync_backoff", retry_at)
                cancel_connection(db, connection)
                generation = connection.generation
            connection.generation = generation
            connection.history_days = history_days
            connection.status = "checking"
            connection.connect_attempt_at = current
            connection.updated_at = current

        try:
            credential = self.provider.connect(login, password)
            encrypted = self.vault.seal(athlete_id, generation, credential)
        except ProviderError as error:
            with self.transaction() as db:
                connection = db.get(OnelapConnection, athlete_id)
                if connection and connection.generation == generation and connection.status == "checking":
                    connection.status = problem_status(error.code)
                    connection.diagnostic = error.code
                    connection.next_sync_at = max(time() + 60, error.retry_at or 0)
                    connection.failures = 1
            raise

        account_hash = digest(self.provider.origin, credential.subject)
        with self.transaction() as db:
            verify_actor(db, actor)
            connection = db.get(OnelapConnection, athlete_id)
            if connection is None or connection.generation != generation or connection.status != "checking":
                raise ProviderError("connection_changed")
            conflict = db.scalar(select(OnelapConnection.athlete_id).where(
                OnelapConnection.account_hash == account_hash, OnelapConnection.athlete_id != athlete_id,
            ))
            if conflict is not None:
                connection.status = "not_connected"
                connection.diagnostic = "account_already_linked"
            else:
                connection.secret = encrypted
                connection.account_hash = account_hash
                connection.status = "connected"
                connection.next_sync_at = time()
                audit(db, "onelap_connected", athlete_id, athlete_id)
        if conflict is not None:
            raise ProviderError("account_already_linked")

    def disconnect(self, actor):
        athlete_id = actor.user_id
        with self.transaction() as db:
            verify_actor(db, actor)
            if actor.role != "athlete":
                raise ProviderError("role_denied")
            connection = db.get(OnelapConnection, athlete_id)
            if connection is None:
                return
            old_secret, generation = connection.secret, connection.generation
            cancel_connection(db, connection)
            audit(db, "onelap_disconnected", athlete_id, athlete_id)
        if old_secret and self.available:
            try:
                credential = self.vault.open(athlete_id, generation, old_secret)
                self.provider.disconnect(credential)
            except ProviderError:
                with self.transaction() as db:
                    connection = db.get(OnelapConnection, athlete_id)
                    if connection and connection.generation == generation + 1:
                        connection.diagnostic = "revocation_unconfirmed"

    def enqueue(self, db, athlete_id, trigger, actor_id):
        self.require_available()
        connection = db.get(OnelapConnection, athlete_id)
        if connection is None or not connection.secret or connection.status in {"reauthorization", "interaction_required"}:
            raise ProviderError("not_connected")
        user = db.get(User, athlete_id)
        if not user or not user.active:
            raise ProviderError("not_connected")
        run = db.scalar(select(SyncRun).where(
            SyncRun.athlete_id == athlete_id, SyncRun.status.in_(ACTIVE_RUNS),
        ).limit(1))
        if run:
            if run.status == "queued" and run.trigger == "verify" and trigger == "manual":
                run.trigger = "manual"
            return run.id
        current = time()
        if connection.failures and connection.next_sync_at and connection.next_sync_at > current:
            raise ProviderError("sync_backoff", connection.next_sync_at)
        last = db.scalar(select(SyncRun).where(
            SyncRun.athlete_id == athlete_id, SyncRun.generation == connection.generation,
            SyncRun.trigger.in_(("manual", "verify")),
        ).order_by(SyncRun.id.desc()).limit(1))
        if last and current - last.created_at < 60:
            raise ProviderError("sync_backoff", last.created_at + 60)
        run = self._queued(db, connection, trigger)
        audit(db, "onelap_sync_requested", actor_id, athlete_id)
        return run.id

    @staticmethod
    def _queued(db, connection, trigger):
        run = SyncRun(athlete_id=connection.athlete_id, generation=connection.generation, trigger=trigger, status="queued")
        db.add(run)
        db.flush()
        return run

    def recover(self):
        with self.transaction() as db:
            for run in db.scalars(select(SyncRun).where(SyncRun.status == "running")):
                run.status, run.ended_at, run.diagnostic = "interrupted", time(), "interrupted"
                db.add(SyncError(run_id=run.id, code="interrupted"))
                connection = db.get(OnelapConnection, run.athlete_id)
                if connection and connection.generation == run.generation and connection.secret:
                    connection.status = "connected"
                    connection.next_sync_at = time()
                    self._queued(db, connection, run.trigger)

    def _claim(self):
        with self.transaction() as db:
            current = time()
            db.execute(update(OnelapConnection).where(
                OnelapConnection.status == "checking", OnelapConnection.updated_at < current - CHECK_TIMEOUT,
            ).values(status="temporary_error", diagnostic="interrupted"))
            run = db.scalar(
                select(SyncRun).join(OnelapConnection, SyncRun.athlete_id == OnelapConnection.athlete_id)
                .join(User, User.id == SyncRun.athlete_id)
                .where(SyncRun.status == "queued", SyncRun.generation == OnelapConnection.generation,
                       OnelapConnection.secret.is_not(None), User.active.is_(True))
                .order_by(SyncRun.id).limit(1)
            )
            if run is None:
                connection = db.scalar(select(OnelapConnection).join(User, User.id == OnelapConnection.athlete_id)
                    .where(OnelapConnection.secret.is_not(None), User.active.is_(True),
                           OnelapConnection.next_sync_at <= current)
                    .order_by(OnelapConnection.next_sync_at, OnelapConnection.athlete_id).limit(1))
                if connection is None:
                    return None
                run = self._queued(db, connection, "automatic")
            connection = db.get(OnelapConnection, run.athlete_id)
            run.status, run.started_at = "running", current
            connection.status = "syncing"
            return Claim(run.id, run.athlete_id, run.generation, run.trigger, connection.secret,
                         connection.history_days, connection.watermark)

    @staticmethod
    def _active(db, claim):
        connection = db.get(OnelapConnection, claim.athlete_id)
        run = db.get(SyncRun, claim.id)
        if (connection is None or connection.generation != claim.generation or not connection.secret
                or run is None or run.status != "running"):
            raise ProviderError("connection_changed")
        return connection, run

    def _record(self, claim, source_id, revision_hash, result, code=None):
        with self.transaction() as db:
            _, run = self._active(db, claim)
            entry = db.get(OnelapRevision, (claim.athlete_id, source_id))
            if entry is None:
                entry = OnelapRevision(athlete_id=claim.athlete_id, source_id=source_id)
                db.add(entry)
            entry.revision_hash = revision_hash
            if code:
                run.rejected += 1
                db.add(SyncError(run_id=claim.id, code=code))
            elif result == "duplicate":
                run.duplicates += 1
            else:
                run.imported += 1

    def _finish(self, claim, watermark=None, error=None):
        with self.transaction() as db:
            try:
                connection, run = self._active(db, claim)
            except ProviderError:
                return
            current = time()
            run.ended_at = current
            if error:
                run.status, run.diagnostic = "failed", error.code
                db.add(SyncError(run_id=run.id, code=error.code))
                connection.status = problem_status(error.code)
                connection.diagnostic = error.code
                connection.failures += 1
                delay = min(RETRY_LIMIT_SECONDS, 60 * 2 ** min(connection.failures - 1, 10))
                connection.next_sync_at = (
                    max(current + delay, error.retry_at or 0) if connection.status == "temporary_error" else None
                )
                if connection.status in {"reauthorization", "interaction_required"}:
                    connection.secret = None
                if error.code == "interrupted":
                    run.status = "interrupted"
                    connection.next_sync_at = current
            else:
                run.status = "partial" if run.rejected else "complete"
                run.diagnostic = "fit_rejected" if run.rejected else None
                connection.status = "temporary_error" if run.rejected else "synced" if watermark is not None else "connected"
                connection.diagnostic = run.diagnostic
                connection.failures = 0
                if watermark is not None:
                    connection.watermark = watermark
                    if not run.rejected:
                        connection.last_success_at = current
                    connection.next_sync_at = current + self.interval
                elif connection.next_sync_at is None:
                    connection.next_sync_at = current

    def run_one(self):
        if not self.available or self.stopped.is_set() or (claim := self._claim()) is None:
            return False
        try:
            self._execute(claim)
        except ProviderError as error:
            self._finish(claim, error=error)
        except Exception:
            # Persist a controlled diagnostic without retaining the exception or response.
            self._finish(claim, error=ProviderError("internal_sync_error"))
        return True

    def _execute(self, claim):
        credential = self.vault.open(claim.athlete_id, claim.generation, claim.secret)
        renewed = False

        def invoke(operation, *args, **kwargs):
            nonlocal credential, renewed
            if self.stopped.is_set():
                raise ProviderError("interrupted")
            with Session(self.engine) as db:
                self._active(db, claim)
            try:
                return operation(credential, *args, **kwargs)
            except ProviderError as exc:
                if exc.code != "session_expired":
                    raise
                if renewed:
                    raise ProviderError("reauthorization") from None
            renewed = True
            credential = self.provider.refresh(credential)
            with self.transaction() as db:
                connection, _ = self._active(db, claim)
                connection.secret = self.vault.seal(claim.athlete_id, claim.generation, credential)
            try:
                return operation(credential, *args, **kwargs)
            except ProviderError as exc:
                if exc.code == "session_expired":
                    raise ProviderError("reauthorization") from None
                raise

        if claim.trigger == "verify":
            invoke(self.provider.check)
            self._finish(claim)
            return
        upper = max(int(time()), claim.watermark or 0)
        started_from = upper - claim.history_days * 86400 if claim.watermark is None else None
        changed_from = max(0, claim.watermark - OVERLAP_SECONDS) if claim.watermark is not None else None
        cursor, seen = None, set()
        unavailable = False
        for _ in range(MAX_PAGES):
            page = invoke(
                self.provider.list_workouts, started_from=started_from, changed_from=changed_from,
                through=upper, cursor=cursor,
            )
            for item in page.workouts:
                if (started_from or 0) <= item.started_at <= upper:
                    try:
                        self._sync_workout(claim, credential.subject, item, invoke)
                    except ProviderError as exc:
                        if exc.code != "fit_unavailable":
                            raise
                        unavailable = True
            if page.cursor is None:
                if unavailable:
                    raise ProviderError("fit_unavailable")
                self._finish(claim, upper)
                return
            if page.cursor in seen:
                raise ProviderError("invalid_response")
            cursor = page.cursor
            seen.add(cursor)
        raise ProviderError("invalid_response")

    def _sync_workout(self, claim, subject, item, invoke):
        source_id = digest(subject, item.id)
        revision_hash = digest(item.revision)
        with Session(self.engine) as db:
            self._active(db, claim)
            entry = db.get(OnelapRevision, (claim.athlete_id, source_id))
            if entry and entry.revision_hash == revision_hash:
                return
        try:
            payload = invoke(self.provider.download_fit, item)
            result = self.importer.import_fit(
                claim.athlete_id, payload, filename="onelap.fit", source="onelapfit", external_id=source_id,
                guard=lambda db: self._active(db, claim),
            )
        except FitProblem as exc:
            if exc.status not in {409, 413, 422}:
                raise ProviderError("fit_import_error") from None
            self._record(claim, source_id, revision_hash, None, exc.code)
        except ProviderError as exc:
            if exc.code != "fit_too_large":
                raise
            self._record(claim, source_id, revision_hash, None, exc.code)
        else:
            self._record(claim, source_id, revision_hash, result.status)
