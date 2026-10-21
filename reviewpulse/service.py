from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from . import demo
from .config import Settings
from .ingest import normalize
from .store import Store
from .util import UserError


class Service:
    def __init__(self, settings: Settings, store: Store):
        self.settings, self.store = settings, store

    def submit(self, rows: list[dict], name: str, question: str = "", source_ids: list[str] | None = None, request_key: str | None = None, is_demo: bool = False) -> dict:
        reviews, validation = normalize(rows, self.settings)
        available = self.store.sources()
        if source_ids is None:
            sources = [s for s in available if not s["is_demo"]]
        else:
            by_id = {s["id"]: s for s in available}
            if len(source_ids) != len(set(source_ids)) or any(sid not in by_id for sid in source_ids):
                raise UserError("Unknown or duplicate source IDs. Refresh the Knowledge page.")
            sources = [by_id[sid] for sid in sorted(source_ids)]
            if not is_demo and any(s["is_demo"] for s in sources):
                raise UserError("Synthetic demo sources cannot be attached to real analysis jobs.")
        spec = {
            "reviews": reviews, "validation": validation, "sources": sources,
            "question": question, "product": {"name": "DemoDesk" if is_demo else self.settings.product_name, "description": "A fictional team-workspace application" if is_demo else self.settings.product_description},
            "taxonomy": self.settings.taxonomy(), "mode": "local" if is_demo else self.settings.analysis_mode,
            "model": self.settings.llm_model, "target": self.settings.target(), "is_demo": is_demo,
        }
        # Demo reports always remain local, even on an MCP-configured deployment.
        # Approval will reject a target mismatch rather than sending sample data.
        if is_demo:
            spec["target"] = {"mode": "local", "document_id": "local-demo-document", "recipient": "local-demo@example.invalid", "endpoint_hash": "local"}
        return self.store.create_job(name, spec, request_key)

    def demo(self) -> dict:
        source = next((s for s in self.store.sources() if s["is_demo"] and s["content"] == demo.DEMO_DOC), None)
        if source is None:
            source = self.store.add_source("DemoDesk sample billing policy (synthetic)", "", demo.DEMO_DOC, True)
        return self.submit(demo.rows(datetime.now(ZoneInfo(self.settings.timezone)).date()), "Synthetic demo - 12 weeks", "How do subscription renewals, cancellation, and duplicate billing charges work?", [source["id"]], is_demo=True)

    def summary(self, job: dict) -> dict:
        result = {key: job.get(key) for key in ["id", "name", "status", "created_at", "updated_at", "error", "progress"]}
        result["status_url"] = self.settings.public_url + "/api/jobs/" + job["id"]
        result["report_url"] = self.settings.public_url + "/#report/" + job["id"]
        return result

    def details(self, identifier: str) -> dict:
        job = self.store.job(identifier)
        result = self.summary(job)
        result["result"] = json.loads(job["result_json"]) if job["result_json"] else None
        approval = self.store.approval_for_job(identifier)
        result["approval"] = self.safe_approval(approval) if approval else None
        return result

    @staticmethod
    def safe_approval(approval: dict) -> dict:
        return {key: approval.get(key) for key in ["id", "job_id", "payload_hash", "status", "approved_at", "approved_by", "doc_state", "draft_state", "receipt_json", "error"]}
