from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .util import UserError, canonical, digest, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL,
 content TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
 is_demo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 request_key TEXT UNIQUE, fingerprint TEXT NOT NULL,
 spec_json TEXT NOT NULL, result_json TEXT, error TEXT,
 progress TEXT NOT NULL DEFAULT 'Queued', owner TEXT, lease_until REAL
);
CREATE TABLE IF NOT EXISTS approvals (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
 payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL,
 status TEXT NOT NULL, approved_at TEXT NOT NULL, approved_by TEXT NOT NULL,
 doc_state TEXT NOT NULL DEFAULT 'pending', draft_state TEXT NOT NULL DEFAULT 'pending',
 receipt_json TEXT, error TEXT, owner TEXT, lease_until REAL
);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
 event TEXT NOT NULL, entity_id TEXT NOT NULL, detail_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS local_effects (
 marker TEXT NOT NULL, kind TEXT NOT NULL, payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(marker, kind)
);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS ix_approval_status ON approvals(status);
"""


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript(SCHEMA)
        try:
            path.chmod(0o600)
        except OSError:
            pass

    @contextmanager
    def connect(self, transaction: bool = False):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA busy_timeout = 15000")
        try:
            if transaction:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if transaction:
                db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    @staticmethod
    def event(db, event: str, entity: str, detail: dict | None = None):
        db.execute("INSERT INTO audit(at,event,entity_id,detail_json) VALUES(?,?,?,?)", (now(), event, entity, canonical(detail or {})))

    def add_source(self, title: str, url: str, content: str, is_demo: bool = False) -> dict:
        identifier = "S" + uuid.uuid4().hex[:12]
        item = {"id": identifier, "title": title, "url": url, "content": content, "sha256": digest(content), "created_at": now(), "is_demo": int(is_demo)}
        with self.connect(True) as db:
            count = db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
            if count >= 50:
                raise UserError("The source limit is 50. Delete unused sources first.")
            db.execute("INSERT INTO sources VALUES(:id,:title,:url,:content,:sha256,:created_at,:is_demo)", item)
            self.event(db, "source_added", identifier, {"sha256": item["sha256"]})
        return item

    def sources(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM sources ORDER BY created_at DESC, id")]

    def delete_source(self, identifier: str):
        with self.connect(True) as db:
            if db.execute("DELETE FROM sources WHERE id=?", (identifier,)).rowcount != 1:
                raise UserError("Source not found.")
            self.event(db, "source_deleted", identifier)

    def create_job(self, name: str, spec: dict, request_key: str | None = None) -> dict:
        fingerprint = digest({"name": name, "spec": spec})
        with self.connect(True) as db:
            if request_key:
                existing = db.execute("SELECT * FROM jobs WHERE request_key=?", (request_key,)).fetchone()
                if existing:
                    if existing["fingerprint"] != fingerprint:
                        raise UserError("Idempotency key already belongs to different input or configuration. Use a new key.")
                    return dict(existing)
            pending = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            if pending >= 20:
                raise UserError("The job queue is full. Wait for existing jobs to finish.")
            identifier = uuid.uuid4().hex
            timestamp = now()
            db.execute("INSERT INTO jobs(id,name,status,created_at,updated_at,request_key,fingerprint,spec_json) VALUES(?,?,'queued',?,?,?,?,?)", (identifier, name, timestamp, timestamp, request_key, fingerprint, canonical(spec)))
            self.event(db, "job_queued", identifier, {"fingerprint": fingerprint, "reviews": len(spec["reviews"])})
        return self.job(identifier)

    def job(self, identifier: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise UserError("Job not found.")
            return dict(row)

    def jobs(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT id,name,status,created_at,updated_at,error,progress FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT 200")]

    def claim_job(self, owner: str) -> dict | None:
        with self.connect(True) as db:
            # A lost analysis lease is safely requeued: analysis has no external writes.
            db.execute("UPDATE jobs SET status='queued',owner=NULL,progress='Recovered after interrupted analysis' WHERE status='running' AND lease_until<?", (time.time(),))
            row = db.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE jobs SET status='running',owner=?,lease_until=?,updated_at=?,progress='Preparing analysis' WHERE id=?", (owner, time.time() + 90, now(), row["id"]))
            return dict(db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())

    def progress(self, identifier: str, owner: str, message: str):
        with self.connect(True) as db:
            changed = db.execute("UPDATE jobs SET progress=?,updated_at=? WHERE id=? AND owner=? AND status='running'", (message, now(), identifier, owner)).rowcount
            if not changed:
                raise UserError("Analysis lease was lost; this worker will not save a result.")

    def complete_job(self, identifier: str, owner: str, result: dict):
        with self.connect(True) as db:
            changed = db.execute("UPDATE jobs SET status='ready',result_json=?,progress='Ready for review',owner=NULL,lease_until=NULL,updated_at=? WHERE id=? AND owner=? AND status='running'", (canonical(result), now(), identifier, owner)).rowcount
            if not changed:
                raise UserError("Analysis lease lost before saving the result.")
            self.event(db, "analysis_ready", identifier, {"payload_hash": result["payload_hash"]})

    def fail_job(self, identifier: str, owner: str, error: str):
        with self.connect(True) as db:
            db.execute("UPDATE jobs SET status='failed',error=?,progress='Failed',owner=NULL,lease_until=NULL,updated_at=? WHERE id=? AND owner=?", (error, now(), identifier, owner))
            self.event(db, "analysis_failed", identifier, {"error": error})

    def heartbeat(self, owner: str):
        with self.connect(True) as db:
            for table in ("jobs", "approvals"):
                db.execute(f"UPDATE {table} SET lease_until=? WHERE owner=?", (time.time() + 90, owner))

    def approve(self, job_id: str, expected_hash: str, target: dict) -> dict:
        with self.connect(True) as db:
            job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job or job["status"] != "ready":
                raise UserError("Only a completed, non-rejected report can be approved.")
            result = json.loads(job["result_json"])
            payload = result["payload"]
            if digest(payload) != expected_hash or result["payload_hash"] != expected_hash:
                raise UserError("Preview changed or hash mismatch. Reload the report before approving.")
            if payload["target"] != target:
                raise UserError("Publishing destinations changed. Create a new analysis and review its new preview.")
            if target["mode"] == "disabled":
                raise UserError("Publishing is disabled in this deployment.")
            existing = db.execute("SELECT * FROM approvals WHERE job_id=?", (job_id,)).fetchone()
            if existing:
                return dict(existing)
            identifier = uuid.uuid4().hex
            db.execute("INSERT INTO approvals(id,job_id,payload_json,payload_hash,status,approved_at,approved_by) VALUES(?,?,?,?,'queued',?,'owner')", (identifier, job_id, canonical(payload), expected_hash, now()))
            self.event(db, "approved", identifier, {"job_id": job_id, "payload_hash": expected_hash})
            return dict(db.execute("SELECT * FROM approvals WHERE id=?", (identifier,)).fetchone())

    def reject(self, job_id: str):
        with self.connect(True) as db:
            if db.execute("SELECT 1 FROM approvals WHERE job_id=?", (job_id,)).fetchone():
                raise UserError("Already approved. Publishing may have started; rejection cannot roll back external writes.")
            if db.execute("UPDATE jobs SET status='rejected' WHERE id=? AND status='ready'", (job_id,)).rowcount != 1:
                raise UserError("Only a ready report can be rejected.")
            self.event(db, "rejected", job_id)

    def approval_for_job(self, job_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM approvals WHERE job_id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def approval(self, identifier: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM approvals WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise UserError("Approval not found.")
            return dict(row)

    def claim_approval(self, owner: str) -> dict | None:
        with self.connect(True) as db:
            stale = db.execute("SELECT * FROM approvals WHERE status='publishing' AND lease_until<?", (time.time(),)).fetchall()
            for row in stale:
                db.execute("UPDATE approvals SET status='needs_attention',owner=NULL,doc_state=CASE WHEN doc_state='in_flight' THEN 'uncertain' ELSE doc_state END,draft_state=CASE WHEN draft_state='in_flight' THEN 'uncertain' ELSE draft_state END,error='Worker interrupted. Reconcile external effects before retrying.' WHERE id=?", (row["id"],))
                self.event(db, "publish_interrupted", row["id"])
            row = db.execute("SELECT * FROM approvals WHERE status='queued' ORDER BY approved_at LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE approvals SET status='publishing',owner=?,lease_until=? WHERE id=?", (owner, time.time() + 90, row["id"]))
            return dict(db.execute("SELECT * FROM approvals WHERE id=?", (row["id"],)).fetchone())

    def step(self, identifier: str, owner: str, field: str, state: str, receipt: dict | None = None):
        if field not in {"doc_state", "draft_state"} or state not in {"in_flight", "done", "uncertain"}:
            raise ValueError("Invalid publishing step.")
        with self.connect(True) as db:
            row = db.execute("SELECT * FROM approvals WHERE id=? AND owner=? AND status='publishing' AND lease_until>?", (identifier, owner, time.time())).fetchone()
            if not row:
                raise UserError("Publishing lease lost. Do not repeat external writes without reconciliation.")
            receipts = json.loads(row["receipt_json"] or "{}")
            if receipt is not None:
                receipts[field] = receipt
            db.execute(f"UPDATE approvals SET {field}=?,receipt_json=? WHERE id=?", (state, canonical(receipts), identifier))
            self.event(db, "publish_step", identifier, {"step": field, "state": state})

    def finish_approval(self, identifier: str, owner: str, error: str | None = None):
        with self.connect(True) as db:
            row = db.execute("SELECT * FROM approvals WHERE id=? AND owner=?", (identifier, owner)).fetchone()
            if not row:
                return
            done = row["doc_state"] == row["draft_state"] == "done"
            state = "published" if done else "needs_attention"
            db.execute("UPDATE approvals SET status=?,error=?,owner=NULL,lease_until=NULL WHERE id=?", (state, None if done else error, identifier))
            self.event(db, state, identifier, {"error": None if done else error})

    def reconcile(self, identifier: str, doc_state: str, draft_state: str, note: str):
        if doc_state not in {"done", "pending"} or draft_state not in {"done", "pending"} or len(note.strip()) < 15:
            raise UserError("Provide done/pending for both steps and a 15+ character verification note.")
        with self.connect(True) as db:
            row = db.execute("SELECT * FROM approvals WHERE id=?", (identifier,)).fetchone()
            if not row or row["status"] != "needs_attention":
                raise UserError("Only a needs_attention approval can be reconciled.")
            for field, requested in (("doc_state", doc_state), ("draft_state", draft_state)):
                if row[field] == "done" and requested != "done":
                    raise UserError("A confirmed completed step cannot be reset for replay.")
            if draft_state == "done" and doc_state != "done":
                raise UserError("A completed draft requires a completed document step.")
            status = "published" if doc_state == draft_state == "done" else "queued"
            db.execute("UPDATE approvals SET doc_state=?,draft_state=?,status=?,error=NULL WHERE id=?", (doc_state, draft_state, status, identifier))
            self.event(db, "manually_reconciled", identifier, {"doc_state": doc_state, "draft_state": draft_state, "note": note})

    def audits(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 200")]
