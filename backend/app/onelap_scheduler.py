import logging
from threading import Event, Thread
from time import time

import portalocker
from sqlalchemy import text
from sqlalchemy.orm import Session

from .onelap_models import OnelapGate
from .onelap_provider import ProviderError

logger = logging.getLogger(__name__)


class RequestGate:
    def __init__(self, engine, interval: float, stopped: Event):
        self.engine = engine
        self.interval = interval
        self.stopped = stopped

    def reserve(self):
        while not self.stopped.is_set():
            with Session(self.engine) as db:
                db.execute(text("BEGIN IMMEDIATE"))
                current = time()
                gate = db.get(OnelapGate, 1)
                if gate is None:
                    gate = OnelapGate(id=1, next_request_at=0, blocked_until=0)
                    db.add(gate)
                if gate.blocked_until > current:
                    raise ProviderError("rate_limited", gate.blocked_until)
                delay = gate.next_request_at - current
                if delay <= 0:
                    gate.next_request_at = current + self.interval
                    db.commit()
                    return
            self.stopped.wait(delay)
        raise ProviderError("interrupted")

    def block(self, until: float):
        with Session(self.engine) as db:
            db.execute(text("BEGIN IMMEDIATE"))
            gate = db.get(OnelapGate, 1)
            if gate is None:
                gate = OnelapGate(id=1, next_request_at=0, blocked_until=0)
                db.add(gate)
            gate.blocked_until = max(gate.blocked_until, until)
            db.commit()


class OnelapScheduler:
    def __init__(self, service, lock_path, stopped: Event, poll_seconds: float = 2):
        self.service = service
        self.lock_path = lock_path
        self.stopped = stopped
        self.poll_seconds = poll_seconds
        self.thread = Thread(target=self._run, daemon=True, name="onelap-sync")

    def start(self):
        self.thread.start()

    def close(self):
        self.stopped.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=45)

    def leader_lock(self):
        return portalocker.Lock(self.lock_path, mode="a", timeout=0, fail_when_locked=True)

    def _run(self):
        while not self.stopped.is_set():
            try:
                with self.leader_lock():
                    self.service.recover()
                    while not self.stopped.is_set():
                        if not self.service.run_one():
                            self.stopped.wait(self.poll_seconds)
            except portalocker.exceptions.AlreadyLocked:
                self.stopped.wait(self.poll_seconds)
            except Exception:
                # Use a fixed message. Transport exceptions may contain private URL parameters.
                logger.error("Onelap scheduler paused after an internal error")
                self.stopped.wait(self.poll_seconds)
