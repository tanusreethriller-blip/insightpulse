from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Protocol

from .config import Settings
from .store import Store
from .util import UserError, canonical, now

CONTRACT = {
    "read_doc": {"document_id"},
    "append_to_doc": {"document_id", "entry"},
    "create_email_draft": {"recipient", "subject", "body"},
}


class ToolClient(Protocol):
    async def call(self, name: str, arguments: dict) -> dict: ...


def validate_tools(tools: list[dict]) -> None:
    by_name = {tool["name"]: tool for tool in tools}
    if len(by_name) != len(tools) or set(by_name) != set(CONTRACT):
        raise UserError("MCP must expose exactly read_doc, append_to_doc, and create_email_draft. Use the supplied workflow.")
    for name, arguments in CONTRACT.items():
        schema = by_name[name].get("inputSchema", {})
        properties = schema.get("properties", {})
        if set(properties) != arguments or not set(schema.get("required", [])).issubset(arguments):
            raise UserError(f"MCP input schema mismatch for {name}. Reimport the supplied workflow without changing parameter names.")
        for argument in arguments:
            if properties[argument].get("type") != "string":
                raise UserError(f"MCP argument {name}.{argument} must be a string.")


class SDKTools:
    def __init__(self, session):
        self.session = session

    async def list_tools(self) -> list[dict]:
        from mcp_types import PaginatedRequestParams
        tools = []
        cursor = None
        seen = set()
        for _ in range(20):
            result = await self.session.list_tools(params=PaginatedRequestParams(cursor=cursor) if cursor else None)
            wire = result.model_dump(by_alias=True, mode="json", exclude_none=True)
            tools.extend(wire["tools"])
            cursor = wire.get("nextCursor")
            if not cursor:
                return tools
            if cursor in seen:
                raise UserError("MCP tool listing repeated a pagination cursor.")
            seen.add(cursor)
        raise UserError("MCP tool listing exceeded the pagination limit.")

    async def call(self, name: str, arguments: dict) -> dict:
        if name not in CONTRACT or set(arguments) != CONTRACT[name]:
            raise UserError("Disallowed MCP tool call.")
        result = await self.session.call_tool(name, arguments, read_timeout_seconds=90)
        wire = result.model_dump(by_alias=True, mode="json", exclude_none=True)
        if wire.get("isError"):
            raise UserError("The n8n tool reported an error. Inspect its execution privately before retrying.")
        return wire


@asynccontextmanager
async def connect_mcp(settings: Settings):
    # Version 2.3 of the SDK uses httpx2 and a TWO-stream transport tuple.
    # Imports are lazy, so local mode does not need an installed MCP SDK.
    try:
        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
    except ImportError as exc:
        raise UserError("Install requirements-mcp.txt to enable live MCP publishing.") from exc
    async with create_mcp_http_client(headers={"Authorization": "Bearer " + settings.mcp_token}) as http_client:
        async with streamable_http_client(settings.mcp_url, http_client=http_client) as streams:
            async with ClientSession(streams[0], streams[1], read_timeout_seconds=90) as session:
                await session.initialize()
                tools = SDKTools(session)
                validate_tools(await tools.list_tools())
                yield tools


def expand(value):
    """Unwrap JSON carried inside MCP TextContent without trusting it as code."""
    if isinstance(value, str):
        try:
            return expand(json.loads(value))
        except (json.JSONDecodeError, RecursionError):
            return value
    if isinstance(value, list):
        return [expand(item) for item in value]
    if isinstance(value, dict):
        return {key: expand(item) for key, item in value.items()}
    return value


def values_for(value, keys: set[str]) -> list[str]:
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and isinstance(item, str):
                found.append(item)
            found.extend(values_for(item, keys))
    elif isinstance(value, list):
        for item in value:
            found.extend(values_for(item, keys))
    return found


def document_text(result: dict, expected_id: str) -> str:
    unpacked = expand(result)
    identifiers = values_for(unpacked, {"documentId", "document_id"})
    if expected_id not in identifiers:
        raise UserError("read_doc did not identify the expected document. Disable Simplify in that n8n node and verify its arguments.")
    # A marker can be split over Google Docs text runs; join text content first.
    texts = values_for(unpacked, {"content", "text"})
    return "".join(texts) + "\n" + canonical(unpacked)


def draft_id(result: dict) -> str:
    ids = values_for(expand(result), {"draft_id", "draftId", "id"})
    if not ids:
        raise UserError("Gmail returned no draft identifier. The write may have succeeded; inspect Drafts before retrying.")
    return ids[0]


class LocalTools:
    """An explicitly labeled local sink; it never contacts Google or n8n."""
    def __init__(self, store: Store, marker: str):
        self.store, self.marker = store, marker

    async def call(self, name: str, arguments: dict) -> dict:
        if name not in CONTRACT or set(arguments) != CONTRACT[name]:
            raise UserError("Disallowed publishing tool call.")
        if name == "read_doc":
            with self.store.connect() as db:
                rows = db.execute("SELECT payload_json FROM local_effects WHERE kind='document' ORDER BY created_at").fetchall()
            return {"documentId": arguments["document_id"], "content": "\n".join(json.loads(row[0])["entry"] for row in rows)}
        kind = "document" if name == "append_to_doc" else "draft"
        with self.store.connect(True) as db:
            db.execute("INSERT OR IGNORE INTO local_effects VALUES(?,?,?,?)", (self.marker, kind, canonical(arguments), now()))
        return {"documentId": arguments.get("document_id")} if kind == "document" else {"id": "local-" + self.marker.split(":")[1]}
