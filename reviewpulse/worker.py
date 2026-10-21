from __future__ import annotations

import asyncio
import logging
import threading
import uuid

from .analysis import analyze
from .config import Settings
from .publish import publish_approval
from .store import Store
from .util import UserError

logger = logging.getLogger(__name__)


class Worker:
    """SQLite-backed queue with leases. No analysis or publishing in HTTP request handlers."""
    def __init__(self, store: Store, settings: Settings):
        self.store, self.settings = store, settings
        self.owner = uuid.uuid4().hex
        self.stop_event = threading.Event()
        self.thread = None
        self.heartbeat_thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True, name="reviewpulse-worker")
        self.heartbeat_thread = threading.Thread(target=self.keepalive, daemon=True, name="reviewpulse-heartbeat")
        self.heartbeat_thread.start()
        self.thread.start()

    def keepalive(self):
        while not self.stop_event.wait(10):
            try:
                self.store.heartbeat(self.owner)
            except Exception:
                logger.error("Worker heartbeat failed; leases protect against stale result commits.")

    def once(self) -> bool:
        approval = self.store.claim_approval(self.owner)
        if approval:
            asyncio.run(publish_approval(self.store, approval, self.owner, self.settings))
            return True
        job = self.store.claim_job(self.owner)
        if not job:
            return False
        try:
            result = analyze(job, self.settings, lambda message: self.store.progress(job["id"], self.owner, message))
            self.store.complete_job(job["id"], self.owner, result)
        except Exception as exc:
            message = str(exc) if isinstance(exc, UserError) else "Analysis failed unexpectedly. No external publication occurred. Run the tests and inspect configuration."
            logger.error("Analysis job %s failed (%s).", job["id"], type(exc).__name__)
            self.store.fail_job(job["id"], self.owner, message)
        return True

    def run(self):
        while not self.stop_event.is_set():
            try:
                busy = self.once()
            except Exception:
                logger.error("Worker iteration failed; queued work remains in SQLite.")
                busy = False
            if not busy:
                self.stop_event.wait(1)

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
        if self.heartbeat_thread:
            self.heartbeat_thread.join(timeout=1)
        # In-flight work remains leased. A subsequent worker safely requeues
        # analysis or requests manual reconciliation for interrupted publishing.
