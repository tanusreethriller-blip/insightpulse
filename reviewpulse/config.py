from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    ingest_token: str
    public_url: str = "http://127.0.0.1:8000"
    product_name: str = "YourProduct"
    product_description: str = "A software product used by customers."
    analysis_mode: str = "local"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "openai/gpt-oss-20b"
    publish_mode: str = "local"
    mcp_url: str = ""
    mcp_token: str = ""
    document_id: str = ""
    recipient: str = ""
    max_rows: int = 5000
    min_reviews: int = 5
    max_upload_bytes: int = 5 * 1024 * 1024
    lookback_weeks: int = 12
    timezone: str = "Asia/Kolkata"
    worker_enabled: bool = True
    theme_file: Path = ROOT / "config" / "themes.json"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(ROOT / ".env", override=False)
        env = os.environ
        return cls(
            data_dir=Path(env.get("DATA_DIR", str(ROOT / "var"))).expanduser().resolve(),
            ingest_token=env.get("INGEST_API_TOKEN", ""),
            public_url=env.get("PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/"),
            product_name=env.get("PRODUCT_NAME", "YourProduct"),
            product_description=env.get("PRODUCT_DESCRIPTION", "A software product used by customers."),
            analysis_mode=env.get("ANALYSIS_MODE", "local"),
            llm_api_key=env.get("LLM_API_KEY", ""),
            llm_base_url=env.get("LLM_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/"),
            llm_model=env.get("LLM_MODEL", "openai/gpt-oss-20b"),
            publish_mode=env.get("PUBLISH_MODE", "local"),
            mcp_url=env.get("N8N_MCP_URL", ""),
            mcp_token=env.get("N8N_MCP_TOKEN", ""),
            document_id=env.get("GOOGLE_DOC_ID", ""),
            recipient=env.get("REPORT_RECIPIENT", ""),
            max_rows=int(env.get("MAX_REVIEWS", "5000")),
            min_reviews=int(env.get("MIN_REVIEWS", "5")),
            timezone=env.get("TIMEZONE", "Asia/Kolkata"),
            worker_enabled=env.get("WORKER_ENABLED", "true").lower() == "true",
        )

    def validate(self) -> None:
        if len(self.ingest_token) < 32:
            raise ValueError("Run python scripts/init_env.py, or set a 32+ character INGEST_API_TOKEN.")
        if self.analysis_mode not in {"local", "llm"}:
            raise ValueError("ANALYSIS_MODE must be local or llm.")
        if self.publish_mode not in {"disabled", "local", "mcp"}:
            raise ValueError("PUBLISH_MODE must be disabled, local, or mcp.")
        for label, url in [("PUBLIC_URL", self.public_url), ("LLM_BASE_URL", self.llm_base_url)]:
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
                raise ValueError(f"{label} must be an HTTP(S) URL without credentials, query, or fragment.")
        if len(self.product_name) > 100 or "\n" in self.product_name or "\r" in self.product_name:
            raise ValueError("PRODUCT_NAME must be a single line of at most 100 characters.")
        if self.analysis_mode == "llm" and not self.llm_api_key:
            raise ValueError("Set LLM_API_KEY before selecting llm mode.")
        if self.publish_mode == "mcp":
            p = urlsplit(self.mcp_url)
            if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.query or p.fragment:
                raise ValueError("Set N8N_MCP_URL to the exact production Streamable HTTP URL.")
            if p.scheme != "https" and p.hostname not in {"localhost", "127.0.0.1"}:
                raise ValueError("Remote MCP endpoints must use HTTPS.")
            if len(self.mcp_token) < 24:
                raise ValueError("Set a random N8N_MCP_TOKEN of at least 24 characters.")
            if not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", self.document_id):
                raise ValueError("GOOGLE_DOC_ID must be the document ID, not its URL.")
            if not re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", self.recipient):
                raise ValueError("REPORT_RECIPIENT must be one email address.")
        if not 1 <= self.min_reviews <= self.max_rows <= 50000:
            raise ValueError("Invalid review limits.")

    def taxonomy(self) -> list[dict]:
        themes = json.loads(self.theme_file.read_text(encoding="utf-8"))
        ids = [theme["id"] for theme in themes]
        if len(ids) != len(set(ids)) or "other" not in ids or "praise" not in ids:
            raise ValueError("Taxonomy needs unique IDs, including other and praise.")
        return themes

    def target(self) -> dict:
        # Destinations and transport identity are included in the approval hash.
        # Secrets are NEVER included in a report, payload, or database record.
        import hashlib
        if self.publish_mode == "local":
            return {"mode": "local", "document_id": "local-demo-document", "recipient": "local-demo@example.invalid", "endpoint_hash": "local"}
        return {
            "mode": self.publish_mode,
            "document_id": self.document_id,
            "recipient": self.recipient,
            "endpoint_hash": hashlib.sha256(self.mcp_url.encode()).hexdigest(),
        }
