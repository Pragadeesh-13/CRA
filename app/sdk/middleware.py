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
from fastapi.responses import JSONResponse

from app.sdk.client import TroyClient


def _first(body: Dict[str, Any], *keys: str) -> Optional[Any]:
    for key in keys:
        if body.get(key) is not None and body.get(key) != "":
            return body.get(key)
    return None


async def _json_body(request: Request) -> Dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {"value": body}


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
        body = await _json_body(request)
        complaint = _first(body, "complaint", "query", "text", "message", "ticket", "description", "body")
        if not complaint or not str(complaint).strip():
            return JSONResponse(status_code=400, content={"ok": False, "status": "Failed", "error": "Complaint text is required."})
        context = body.get("context") if isinstance(body.get("context"), dict) else None
        result = await troy_client.resolve_ticket(
            str(complaint),
            customer_id=_first(body, "customer_id", "customerId"),
            order_id=_first(body, "order_id", "orderId"),
            context=context,
            auto_execute=bool(body.get("auto_execute", body.get("autoExecute", True))),
            dry_run=bool(body.get("dry_run", body.get("dryRun", False))),
        )
        return result

    @router.post("/tools/execute")
    async def execute_tool(request: Request):
        body = await _json_body(request)
        tool_name = _first(body, "tool", "name", "tool_name", "toolName", "action", "action_name")
        if not tool_name:
            return JSONResponse(status_code=400, content={"ok": False, "status": "Failed", "error": "Tool name is required."})
        params = body.get("params")
        if not isinstance(params, dict):
            params = body.get("arguments") if isinstance(body.get("arguments"), dict) else body.get("args") if isinstance(body.get("args"), dict) else {}
        result = troy_client.tool_registry.execute(
            str(tool_name),
            params,
            idempotency_key=_first(body, "idempotency_key", "idempotent_key", "idempotencyKey"),
            dry_run=body.get("dry_run", body.get("dryRun")),
        )
        return result

    return router


__all__ = ["create_troy_router"]
