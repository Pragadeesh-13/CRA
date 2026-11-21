"""
FastAPI integration for TroyClient.

``create_troy_router(client)`` returns an :class:`APIRouter` exposing:

  - ``POST /resolve``        — resolve a complaint ticket
  - ``GET  /health``         — product name, registered tools, adapter type
  - ``POST /tools/execute``  — execute a registered tool by name

plus ``GET /tools`` and ``GET /info`` for discovery. Request bodies are
parsed flexibly (``complaint``/``query``/``text`` aliases) and missing
complaints return ``400 {"ok": False, ...}`` rather than a framework 422.

Example:
    from fastapi import FastAPI
    from app.sdk import TroyClient, create_troy_router

    app = FastAPI()
    app.include_router(create_troy_router(TroyClient()))
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Request

from app.http.execution_flags import execution_flag
from app.http.request_json import (
    COMPLAINT_FIELDS, EnvelopeError, envelope_error, read_field, read_json_object, resolution_fields,
)
from app.sdk.client import TroyClient


def create_troy_router(
    client: Optional[TroyClient] = None,
    *,
    prefix: str = "",
    tags: Optional[list] = None,
) -> APIRouter:
    """Build a FastAPI router bound to ``client`` (created if omitted)."""
    troy_client = client if client is not None else TroyClient()
    router = APIRouter(prefix=prefix or "", tags=tags or ["troy"])

    @router.get("/health")
    async def health() -> Dict[str, Any]:
        return troy_client.get_health()

    @router.get("/info")
    async def info() -> Dict[str, Any]:
        return troy_client.get_info()

    @router.get("/tools")
    async def tools() -> Dict[str, Any]:
        return {
            "ok": True,
            "tools": troy_client.list_tools(),
            "registered_tools": troy_client.tool_registry.tool_names(),
        }

    @router.post("/resolve")
    async def resolve(request: Request):
        body = await read_json_object(request)
        if body is None:
            return envelope_error()
        try:
            complaint = read_field(body, *COMPLAINT_FIELDS, kind="text", types=(str,))
            options = resolution_fields(body, request.headers.get("Idempotency-Key"))
        except EnvelopeError as exc:
            return envelope_error(str(exc))
        if not complaint or not complaint.strip():
            return envelope_error("Complaint text is required.")
        return await troy_client.resolve_ticket(
            complaint,
            **options,
            auto_execute=execution_flag(body, "auto_execute", "autoExecute", True),
            dry_run=execution_flag(body, "dry_run", "dryRun", False),
        )

    @router.post("/tools/execute")
    async def execute_tool(request: Request):
        body = await read_json_object(request)
        if body is None:
            return envelope_error()
        try:
            tool_name = read_field(body, "tool", "name", "tool_name", "toolName", "action", "action_name",
                                   kind="text", types=(str,))
            params = read_field(body, "params", "arguments", "args", kind="an object", types=(dict,))
            key = read_field(body, "idempotency_key", "idempotent_key", "idempotencyKey", kind="text", types=(str,))
        except EnvelopeError as exc:
            return envelope_error(str(exc))
        if not tool_name or not tool_name.strip():
            return envelope_error("Tool name is required.")
        return troy_client.tool_registry.execute(
            tool_name,
            params or {},
            idempotency_key=key,
            dry_run=execution_flag(body, "dry_run", "dryRun", None),
        )

    return router


__all__ = ["create_troy_router"]
