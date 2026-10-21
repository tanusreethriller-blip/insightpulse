from __future__ import annotations

import json
import secrets
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit

import anyio
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .config import ROOT, Settings
from .ingest import read_csv, redact
from .mcp_client import connect_mcp
from .service import Service
from .store import Store
from .util import UserError, now
from .worker import Worker


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Submit(StrictModel):
    name: str = Field(default="My reviews", min_length=1, max_length=100)
    reviews: list[dict] | None = None
    csv: str | None = None
    question: str = Field(default="", max_length=500)
    source_ids: list[str] | None = Field(default=None, max_length=50)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")

    @model_validator(mode="after")
    def exactly_one_input(self):
        if (self.reviews is None) == (self.csv is None):
            raise ValueError("Provide reviews OR csv, not both.")
        return self


class SourceInput(StrictModel):
    title: str = Field(min_length=3, max_length=150)
    url: str = Field(default="", max_length=2000)
    content: str = Field(min_length=40, max_length=100000)


class ApprovalInput(StrictModel):
    confirmation: Literal["APPROVE"]
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReconcileInput(StrictModel):
    confirmation: Literal["VERIFIED"]
    doc_state: Literal["done", "pending"]
    draft_state: Literal["done", "pending"]
    note: str = Field(min_length=15, max_length=1000)


async def read_body(request: Request, limit: int) -> bytes:
    body = bytearray()
    async for part in request.stream():
        body.extend(part)
        if len(body) > limit:
            raise HTTPException(413, "Upload is too large.")
    return bytes(body)


async def parse(request: Request, schema, limit: int = 8192):
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(415, "Use Content-Type: application/json.")
    try:
        return schema.model_validate_json(await read_body(request, limit))
    except ValidationError as exc:
        # Do not return Pydantic's raw input, which may contain private data.
        fields = sorted({".".join(str(p) for p in e["loc"]) or "body" for e in exc.errors()})
        raise HTTPException(422, "Invalid request fields: " + ", ".join(fields) + ". Check required formats and lengths.") from exc


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    store = Store(settings.data_dir / "reviewpulse.sqlite3")
    service = Service(settings, store)
    worker = Worker(store, settings)

    @asynccontextmanager
    async def lifespan(app):
        if settings.worker_enabled:
            worker.start()
        yield
        worker.stop()

    app = FastAPI(title="Review Pulse", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.settings, app.state.store, app.state.service, app.state.worker = settings, store, service, worker

    @app.exception_handler(UserError)
    async def user_error(request: Request, exc: UserError):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self' about:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def check_origin(request: Request):
        origin = request.headers.get("origin")
        if origin and origin != settings.public_url:
            raise HTTPException(403, "Origin does not match PUBLIC_URL. Open the configured application URL.")

    def owner(request: Request, mutate: bool = False) -> dict:
        if mutate:
            check_origin(request)
        return {}

    def actor(request: Request, mutate: bool = False) -> str:
        authorization = request.headers.get("authorization")
        if authorization:
            if not secrets.compare_digest(authorization.encode(), ("Bearer " + settings.ingest_token).encode()):
                raise HTTPException(401, "Invalid ingestion token.")
            return "ingest"
        owner(request, mutate)
        return "owner"

    @app.get("/healthz")
    def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/api/me")
    def me(request: Request):
        return {"product_name": settings.product_name, "product_description": settings.product_description, "analysis_mode": settings.analysis_mode, "model": settings.llm_model if settings.analysis_mode == "llm" else None, "publish_mode": settings.publish_mode, "target": settings.target(), "max_reviews": settings.max_rows, "min_reviews": settings.min_reviews, "max_upload_bytes": settings.max_upload_bytes, "public_url": settings.public_url, "worker_enabled": settings.worker_enabled}

    @app.get("/api/jobs")
    def jobs(request: Request):
        owner(request)
        return {"jobs": store.jobs()}

    @app.post("/api/jobs", status_code=202)
    async def submit(request: Request):
        actor(request, True)
        data = await parse(request, Submit, settings.max_upload_bytes)
        rows = read_csv(data.csv.encode()) if data.csv is not None else data.reviews
        job = await anyio.to_thread.run_sync(lambda: service.submit(rows, redact(data.name.strip()), redact(data.question.strip()), data.source_ids, data.idempotency_key))
        return service.summary(job)

    @app.get("/api/jobs/{identifier}")
    def job(request: Request, identifier: str):
        role = actor(request)
        return service.details(identifier) if role == "owner" else service.summary(store.job(identifier))

    @app.post("/api/jobs/{identifier}/approve", status_code=202)
    async def approve(request: Request, identifier: str):
        owner(request, True)
        data = await parse(request, ApprovalInput)
        approval = store.approve(identifier, data.payload_hash, settings.target())
        return service.safe_approval(approval)

    @app.post("/api/jobs/{identifier}/reject")
    async def reject(request: Request, identifier: str):
        owner(request, True)
        store.reject(identifier)
        return {"ok": True}

    @app.get("/api/jobs/{identifier}/report.md")
    def markdown(request: Request, identifier: str):
        owner(request)
        job = store.job(identifier)
        if not job["result_json"]:
            raise HTTPException(409, "The report is not ready.")
        report = json.loads(job["result_json"])["report_markdown"]
        return Response(report, media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="review-pulse-{job["id"]}.md"'})

    @app.post("/api/approvals/{identifier}/reconcile", status_code=202)
    async def reconcile(request: Request, identifier: str):
        owner(request, True)
        data = await parse(request, ReconcileInput)
        store.reconcile(identifier, data.doc_state, data.draft_state, redact(data.note))
        return service.safe_approval(store.approval(identifier))

    @app.get("/api/sources")
    def sources(request: Request):
        owner(request)
        return {"sources": store.sources()}

    @app.post("/api/sources", status_code=201)
    async def add_source(request: Request):
        owner(request, True)
        data = await parse(request, SourceInput, 512 * 1024)
        if data.url:
            url = urlsplit(data.url)
            if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.fragment:
                raise HTTPException(422, "Source URL must be HTTP(S), without credentials or fragment.")
        # No URL fetching: avoids an SSRF surface and leaves source selection with the owner.
        return store.add_source(redact(data.title.strip()), data.url.strip(), redact(data.content.strip()))

    @app.delete("/api/sources/{identifier}")
    async def delete_source(request: Request, identifier: str):
        owner(request, True)
        store.delete_source(identifier)
        return {"ok": True, "note": "Existing report snapshots retain their original evidence."}

    @app.post("/api/demo", status_code=202)
    async def demo_job(request: Request):
        owner(request, True)
        return service.summary(service.demo())

    @app.get("/api/sample.csv")
    def sample_csv(request: Request):
        owner(request)
        from .demo import csv_bytes
        return Response(csv_bytes(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="synthetic-reviews.csv"'})

    @app.get("/api/audit")
    def audit(request: Request):
        owner(request)
        return {"events": store.audits()}

    @app.get("/api/local-effects")
    def local_effects(request: Request):
        owner(request)
        with store.connect() as db:
            rows = db.execute("SELECT * FROM local_effects ORDER BY created_at DESC").fetchall()
        return {"effects": [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]}

    @app.post("/api/integrations/check")
    async def check_mcp(request: Request):
        owner(request, True)
        if settings.publish_mode != "mcp":
            return {"ok": True, "mode": settings.publish_mode, "message": "No external connection was made. Select PUBLISH_MODE=mcp to test n8n."}
        try:
            async with connect_mcp(settings) as tools:
                names = [tool["name"] for tool in await tools.list_tools()]
            return {"ok": True, "mode": "mcp", "tools": names, "message": "Tool names and input schemas match. No tools were executed."}
        except Exception as exc:
            message = str(exc) if isinstance(exc, UserError) else "Cannot connect to MCP. Verify the production Streamable HTTP URL, workflow publication, credentials, and token."
            return JSONResponse({"detail": message}, status_code=502)

    @app.get("/")
    def index():
        return {
            "service": "Review Pulse API",
            "dashboard": "Run `streamlit run app.py` to open the dashboard.",
            "health": "/healthz",
        }

    return app
