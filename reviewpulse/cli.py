from __future__ import annotations

import argparse
import asyncio
import json

from .analysis import analyze
from .config import Settings
from .ingest import read_csv
from .mcp_client import connect_mcp
from .service import Service
from .store import Store
from .worker import Worker


def main():
    parser = argparse.ArgumentParser(description="Review Pulse local operations")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="Queue and analyze a synthetic demonstration, without publishing")
    scan = sub.add_parser("analyze", help="Analyze a CSV; never publishes")
    scan.add_argument("csv")
    scan.add_argument("--name", default="CLI review analysis")
    scan.add_argument("--question", default="")
    sub.add_parser("check-mcp", help="Connect and validate tool schemas; does not call tools")
    sub.add_parser("worker", help="Run a separate queue worker when WORKER_ENABLED=false")
    backup = sub.add_parser("backup", help="Create a transaction-consistent SQLite backup")
    backup.add_argument("destination")
    args = parser.parse_args()
    settings = Settings.from_env()
    settings.validate()
    store = Store(settings.data_dir / "reviewpulse.sqlite3")
    service = Service(settings, store)
    if args.command in {"demo", "analyze"}:
        if args.command == "demo":
            job = service.demo()
        else:
            from pathlib import Path
            file = Path(args.csv)
            if file.stat().st_size > settings.max_upload_bytes:
                raise SystemExit("CSV exceeds the configured upload limit.")
            job = service.submit(read_csv(file.read_bytes()), args.name, args.question)
        # Claim only this analysis job; never publish unrelated queued approvals.
        with store.connect(True) as db:
            owner = "cli-" + job["id"]
            import time
            claim = db.execute("UPDATE jobs SET status='running',owner=?,lease_until=? WHERE id=? AND status='queued'", (owner, time.time() + 90, job["id"]))
        if claim.rowcount != 1:
            print(json.dumps(service.summary(store.job(job["id"])), indent=2))
            print("Another worker has already claimed this job. Follow its status in the dashboard.")
            return
        worker = Worker(store, settings)
        worker.owner = owner
        import threading
        heartbeat = threading.Thread(target=worker.keepalive, daemon=True)
        heartbeat.start()
        try:
            result = analyze(store.job(job["id"]), settings, lambda message: print(message))
            store.complete_job(job["id"], owner, result)
        except Exception:
            store.fail_job(job["id"], owner, "CLI analysis failed. Verify configuration and retry.")
            raise
        finally:
            worker.stop_event.set()
            heartbeat.join(timeout=2)
        print(json.dumps(service.summary(store.job(job["id"])), indent=2))
    elif args.command == "check-mcp":
        if settings.publish_mode != "mcp":
            raise SystemExit("Set PUBLISH_MODE=mcp and configure the n8n endpoint first.")
        async def check():
            async with connect_mcp(settings) as tools:
                return [tool["name"] for tool in await tools.list_tools()]
        try:
            print(json.dumps({"tools": asyncio.run(check()), "writes": 0}, indent=2))
        except Exception:
            raise SystemExit("MCP check failed. Verify the production URL, credentials, token, and requirements-mcp.txt. No publishing tools were called.") from None
    elif args.command == "worker":
        worker = Worker(store, settings)
        worker.start()
        try:
            while worker.thread.is_alive():
                worker.thread.join(timeout=1)
        except KeyboardInterrupt:
            worker.stop()
    elif args.command == "backup":
        import sqlite3
        from pathlib import Path
        target = Path(args.destination).resolve()
        if target.exists() or target == store.path.resolve():
            raise SystemExit("Choose a new backup filename outside the active database.")
        target.parent.mkdir(parents=True, exist_ok=True)
        with store.connect() as source, sqlite3.connect(target) as destination:
            source.backup(destination)
        target.chmod(0o600)
        print("Backup created. Treat it as sensitive data.")


if __name__ == "__main__":
    main()
