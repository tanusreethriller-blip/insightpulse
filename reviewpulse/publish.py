from __future__ import annotations

import json

from .config import Settings
from .mcp_client import LocalTools, ToolClient, connect_mcp, document_text, draft_id
from .store import Store
from .util import UserError, digest


async def execute_steps(store: Store, approval: dict, owner: str, tools: ToolClient):
    payload = json.loads(approval["payload_json"])
    if digest(payload) != approval["payload_hash"]:
        raise UserError("Approved payload integrity check failed. Publishing blocked.")
    marker, target = payload["marker"], payload["target"]
    if approval["doc_state"] != "done":
        if approval["doc_state"] != "pending":
            raise UserError("The document write is uncertain. Manual reconciliation is required.")
        before = await tools.call("read_doc", {"document_id": target["document_id"]})
        text = document_text(before, target["document_id"])
        if marker in text:
            store.step(approval["id"], owner, "doc_state", "done", {"document_id": target["document_id"], "marker_already_present": True})
        else:
            # Persist in-flight BEFORE the external side effect. A lost response is NOT a safe retry.
            store.step(approval["id"], owner, "doc_state", "in_flight")
            try:
                await tools.call("append_to_doc", {"document_id": target["document_id"], "entry": payload["doc_entry"]})
                after = await tools.call("read_doc", {"document_id": target["document_id"]})
                if marker not in document_text(after, target["document_id"]):
                    raise UserError("The document marker was not visible after append. Verify n8n and Google Docs manually.")
                store.step(approval["id"], owner, "doc_state", "done", {"document_id": target["document_id"], "verified_marker": marker})
            except Exception:
                store.step(approval["id"], owner, "doc_state", "uncertain")
                raise
    if approval["draft_state"] != "done":
        if approval["draft_state"] != "pending":
            raise UserError("The Gmail draft write is uncertain. Manual reconciliation is required.")
        store.step(approval["id"], owner, "draft_state", "in_flight")
        try:
            response = await tools.call("create_email_draft", {"recipient": target["recipient"], "subject": payload["subject"], "body": payload["body"]})
            identifier = draft_id(response)
            store.step(approval["id"], owner, "draft_state", "done", {"draft_id": identifier})
        except Exception:
            store.step(approval["id"], owner, "draft_state", "uncertain")
            raise


async def publish_approval(store: Store, approval: dict, owner: str, settings: Settings):
    try:
        payload = json.loads(approval["payload_json"])
        if payload["target"] != settings.target():
            raise UserError("Publishing configuration changed after approval. Restore the approved configuration before retrying.")
        if settings.publish_mode == "local":
            await execute_steps(store, approval, owner, LocalTools(store, payload["marker"]))
        elif settings.publish_mode == "mcp":
            async with connect_mcp(settings) as tools:
                await execute_steps(store, approval, owner, tools)
        else:
            raise UserError("Publishing is disabled.")
    except Exception as exc:
        # Raw provider exceptions can contain URLs, tokens, or private content.
        message = str(exc) if isinstance(exc, UserError) else "MCP connection or tool execution failed. Inspect n8n privately and reconcile uncertain steps; no automatic write retry was attempted."
        store.finish_approval(approval["id"], owner, message)
        return
    store.finish_approval(approval["id"], owner)
