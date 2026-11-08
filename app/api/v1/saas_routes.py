"""
SaaS REST & webhook integration surfaces for Troy.

Mounted into the FastAPI app (see ``app/main.py``) at ``/api/v1/saas``:

  - ``POST /api/v1/saas/resolve``                    — standard JSON resolution
  - ``POST /api/v1/saas/webhooks/{provider}``        — Zendesk / Intercom /
    Stripe / Freshdesk / generic webhooks, normalised then resolved
  - ``GET  /api/v1/saas/info``                       — tenant & capability metadata

The module-level ``router`` uses a lazily-created default
:class:`~app.sdk.TroyClient`. Use :func:`create_saas_router` to mount the
same routes against a custom (e.g. seeded) client.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.sdk.client import TroyClient, _jsonable

logger = logging.getLogger(__name__)

SUPPORTED_PROVIDERS = ["zendesk", "intercom", "stripe", "freshdesk", "generic"]

_default_client: Optional[TroyClient] = None


def get_saas_client() -> TroyClient:
    """Return the shared default TroyClient (created lazily)."""
    global _default_client
    if _default_client is None:
        _default_client = TroyClient()
    return _default_client


# Friendly aliases so callers/tests can find the default client.
get_default_client = get_saas_client
get_client = get_saas_client


def _first(mapping: Any, *keys: str) -> Optional[Any]:
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return None


def _dig(payload: Dict[str, Any], *paths: str) -> Optional[Any]:
    for path in paths:
        current: Any = payload
        ok = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current and current[part] not in (None, ""):
                current = current[part]
            else:
                ok = False
                break
        if ok:
            return current
    return None


# ---------------------------------------------------------------------------
# Webhook normalisation
# ---------------------------------------------------------------------------


def normalize_webhook_payload(provider: str, payload: Any) -> Dict[str, Any]:
    """
    Normalise a provider webhook payload into Troy's canonical shape::

        {"complaint": str, "customer_id": ..., "order_id": ...,
         "transaction_id": ..., "customer_email": ..., "customer_name": ...,
         "subject": ..., "provider": ..., "event_type": ..., "context": {...}}
    """
    provider_name = (provider or "generic").strip().lower() or "generic"
    data: Dict[str, Any] = payload if isinstance(payload, dict) else {"value": payload}

    complaint: Optional[Any] = None
    customer_id: Optional[Any] = None
    order_id: Optional[Any] = None
    transaction_id: Optional[Any] = None
    customer_email: Optional[Any] = None
    customer_name: Optional[Any] = None
    subject: Optional[Any] = None
    event_type: Optional[Any] = None
    extra: Dict[str, Any] = {}

    if provider_name == "zendesk":
        ticket = data.get("ticket") if isinstance(data.get("ticket"), dict) else data
        requester = ticket.get("requester") if isinstance(ticket.get("requester"), dict) else data.get("requester") or {}
        if not isinstance(requester, dict):
            requester = {}
        subject = _first(ticket, "subject", "title") or _first(data, "subject", "title")
        complaint = _first(ticket, "description", "comment", "body", "complaint", "query", "text") or _first(data, "description", "comment", "body", "complaint")
        customer_id = _first(ticket, "customer_id", "requester_id", "requesterId") or _first(requester, "id", "customer_id")
        customer_email = _first(requester, "email", "customer_email") or _first(ticket, "email", "customer_email")
        customer_name = _first(requester, "name", "customer_name") or _first(ticket, "name", "customer_name")
        order_id = _first(ticket, "order_id", "orderId") or _first(data, "order_id")
        event_type = _first(data, "type", "event", "event_type") or _first(ticket, "status")
        extra = {"ticket_id": _first(ticket, "id", "ticket_id")}

    elif provider_name == "intercom":
        item = _dig(data, "data.item", "data.conversation", "conversation", "ticket", "data") or {}
        if not isinstance(item, dict):
            item = {}
        user = _dig(data, "data.user", "user", "contact", "customer") or {}
        if not isinstance(user, dict):
            user = {}
        subject = _first(item, "title", "subject", "name") or _first(data, "subject", "title")
        complaint = (
            _first(item, "body", "description", "complaint", "query", "text", "message")
            or _first(data, "body", "description", "complaint", "query", "text", "message")
        )
        customer_id = _first(item, "customer_id") or _first(user, "id", "customer_id", "external_id")
        customer_email = _first(user, "email", "customer_email") or _first(item, "email")
        customer_name = _first(user, "name", "customer_name") or _first(item, "name")
        order_id = _first(item, "order_id", "orderId") or _first(data, "order_id")
        event_type = _first(data, "type", "event", "event_type", "topic")
        extra = {"conversation_id": _first(item, "id", "conversation_id")}

    elif provider_name == "freshdesk":
        ticket = data.get("ticket") if isinstance(data.get("ticket"), dict) else data
        requester = ticket.get("requester") if isinstance(ticket.get("requester"), dict) else data.get("requester") or {}
        if not isinstance(requester, dict):
            requester = {}
        subject = _first(ticket, "subject", "title") or _first(data, "subject", "title")
        complaint = _first(ticket, "description", "body", "complaint", "query", "text") or _first(data, "description", "body", "complaint")
        customer_id = _first(ticket, "customer_id", "requester_id", "requesterId", "contact_id") or _first(requester, "id", "customer_id")
        customer_email = _first(requester, "email", "customer_email") or _first(ticket, "email", "customer_email")
        customer_name = _first(requester, "name", "customer_name") or _first(ticket, "name", "customer_name")
        order_id = _first(ticket, "order_id", "orderId") or _first(data, "order_id")
        event_type = _first(data, "type", "event", "event_type") or _first(ticket, "status")
        extra = {"ticket_id": _first(ticket, "id", "ticket_id")}

    elif provider_name == "stripe":
        obj = _dig(data, "data.object", "data", "object", "charge", "payment_intent", "invoice") or {}
        if not isinstance(obj, dict):
            obj = {}
        event_type = _first(data, "type", "event", "event_type") or _first(obj, "type", "status")
        metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        amount = obj.get("amount") or obj.get("amount_refunded") or obj.get("total")
        amount_str = ""
        if isinstance(amount, (int, float)) and amount:
            amount_str = f" ${amount / 100:.2f}" if amount >= 100 else f" ${amount:.2f}"
        description = _first(obj, "description", "reason", "statement_descriptor") or _first(data, "description", "")
        subject = _first(obj, "id", "invoice", "customer") and f"Stripe event {event_type or 'charge'}"
        complaint = (
            f"Stripe webhook {event_type or 'event'}: {description or 'charge event'}"
            f"{amount_str} for customer {obj.get('customer') or ''}".strip()
        )
        if "refund" in str(event_type or "").lower() or "refund" in str(description or "").lower():
            complaint = f"Customer was charged twice and requests a refund. {complaint}"
        customer_id = _first(metadata, "customer_id", "customerId") or _first(obj, "customer", "customer_id")
        customer_email = _first(obj, "receipt_email", "email", "customer_email") or _first(metadata, "customer_email", "email")
        order_id = _first(metadata, "order_id", "orderId") or _first(obj, "order_id", "invoice")
        transaction_id = _first(metadata, "transaction_id", "transactionId") or _first(obj, "id", "transaction_id")
        # Stripe ids (ch_..., pi_...) are not TXN- ids; only keep them as the
        # transaction reference when no TXN-style id is present.
        extra = {"stripe_event_id": _first(data, "id", "event_id"), "amount": amount, "currency": obj.get("currency")}

    # Generic (and fallback for every provider).
    if complaint is None:
        complaint = _first(
            data,
            "complaint", "query", "text", "message", "description", "body", "content", "issue",
        ) or _dig(data, "ticket.description", "ticket.body", "ticket.complaint", "conversation.body", "data.item.body", "data.object.description")
    if subject is None:
        subject = _first(data, "subject", "title") or _dig(data, "ticket.subject", "ticket.title", "data.item.title")
    if customer_id is None:
        customer_id = _first(data, "customer_id", "customerId", "user_id", "requester_id") or _dig(data, "customer.id", "user.id", "requester.id", "ticket.requester_id")
    if customer_email is None:
        customer_email = _first(data, "customer_email", "email") or _dig(data, "customer.email", "user.email", "requester.email")
    if customer_name is None:
        customer_name = _first(data, "customer_name", "name") or _dig(data, "customer.name", "user.name", "requester.name")
    if order_id is None:
        order_id = _first(data, "order_id", "orderId") or _dig(data, "customer.order_id", "ticket.order_id", "data.object.metadata.order_id")
    if transaction_id is None:
        transaction_id = _first(data, "transaction_id", "transactionId", "txn_id") or _dig(data, "ticket.transaction_id", "data.object.metadata.transaction_id")
    if event_type is None:
        event_type = _first(data, "type", "event", "event_type", "topic", "action")

    if subject and complaint and str(subject).lower() not in str(complaint).lower():
        complaint_text = f"{subject}: {complaint}"
    else:
        complaint_text = str(complaint or subject or "")

    context: Dict[str, Any] = {"provider": provider_name, "raw": data, **extra}
    if event_type:
        context["event_type"] = event_type

    return _jsonable(
        {
            "complaint": complaint_text,
            "customer_id": customer_id,
            "order_id": order_id,
            "transaction_id": transaction_id,
            "customer_email": customer_email,
            "customer_name": customer_name,
            "subject": subject,
            "provider": provider_name,
            "event_type": event_type,
            "context": context,
        }
    )


# Backwards-friendly aliases
normalize_payload = normalize_webhook_payload
normalize_webhook = normalize_webhook_payload


async def _json_body(request: Request) -> Dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {"value": body}


def _client_for_body(body: Dict[str, Any], base_client: TroyClient) -> TroyClient:
    """Honour a per-request ``config`` override while sharing tenant data."""
    config = body.get("config") if isinstance(body, dict) else None
    if config is None:
        return base_client
    try:
        return TroyClient(
            config=config,
            adapter=base_client.adapter,
            tool_registry=base_client.tool_registry,
            driver=base_client.driver,
        )
    except Exception as exc:
        logger.warning("Ignoring invalid per-request SaaS config: %s", exc)
        return base_client


def _info_payload(client: TroyClient) -> Dict[str, Any]:
    info = client.get_info()
    return {
        **info,
        "ok": True,
        "product_name": client.config.product_name,
        "tenant": info.get("tenant"),
        "capabilities": {
            "resolve": True,
            "webhooks": SUPPORTED_PROVIDERS,
            "supported_providers": SUPPORTED_PROVIDERS,
            "tool_execution": True,
            "tools": client.tool_registry.tool_names(),
            "adapter_type": type(client.adapter).__name__,
            "categories": client.config.category_names(),
            "entity_patterns": list(client.config.entity_patterns.keys()),
            "policies": info.get("policies"),
        },
        "endpoints": [
            "POST /api/v1/saas/resolve",
            "POST /api/v1/saas/webhooks/{provider}",
            "GET /api/v1/saas/info",
        ],
        "providers": SUPPORTED_PROVIDERS,
        "supported_providers": SUPPORTED_PROVIDERS,
    }


def create_saas_router(client: Optional[TroyClient] = None) -> APIRouter:
    """
    Build the SaaS router. With ``client`` the routes close over it;
    without one they resolve the shared default client via dependency
    injection (so tests can override ``get_saas_client``).
    """
    router = APIRouter(prefix="/api/v1/saas", tags=["saas"])
    bound_client = client

    def _resolve_client(dep_client: Optional[TroyClient] = None) -> TroyClient:
        # A bound client wins; otherwise honour FastAPI dependency injection
        # (get_saas_client) so app.dependency_overrides works as expected.
        return bound_client if bound_client is not None else (dep_client or get_saas_client())

    @router.get("/info")
    async def saas_info(dep_client: TroyClient = Depends(get_saas_client)) -> Dict[str, Any]:
        return _info_payload(_resolve_client(dep_client))

    @router.post("/resolve")
    async def saas_resolve(request: Request, dep_client: TroyClient = Depends(get_saas_client)):
        body = await _json_body(request)
        complaint = _first(body, "complaint", "query", "text", "message", "ticket", "description", "body")
        if not complaint or not str(complaint).strip():
            return JSONResponse(status_code=400, content={"ok": False, "status": "Failed", "error": "Complaint text is required."})
        troy_client = _client_for_body(body, _resolve_client(dep_client))
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

    @router.post("/webhooks/{provider}")
    async def saas_webhook(provider: str, request: Request, dep_client: TroyClient = Depends(get_saas_client)):
        body = await _json_body(request)
        normalized = normalize_webhook_payload(provider, body)
        complaint = normalized.get("complaint") or ""
        if not str(complaint).strip():
            return JSONResponse(
                status_code=400,
                content={"ok": False, "status": "Failed", "provider": provider, "error": "Could not extract a complaint from the webhook payload.", "normalized": normalized},
            )
        troy_client = _client_for_body(body, _resolve_client(dep_client))
        context = dict(normalized.get("context") or {})
        if isinstance(body.get("context"), dict):
            context.update(body["context"])
        result = await troy_client.resolve_ticket(
            str(complaint),
            customer_id=normalized.get("customer_id"),
            order_id=normalized.get("order_id"),
            context=context,
            auto_execute=bool(body.get("auto_execute", body.get("autoExecute", True))),
            dry_run=bool(body.get("dry_run", body.get("dryRun", False))),
        )
        return {
            **result,
            "provider": normalized.get("provider") or provider,
            "normalized": normalized,
            "webhook": normalized,
            "complaint": complaint,
            "customer_email": normalized.get("customer_email"),
            "customer_name": normalized.get("customer_name"),
            "subject": normalized.get("subject"),
            "event_type": normalized.get("event_type"),
        }

    return router


# Module-level router mounted by app/main.py.
router = create_saas_router()
saas_router = router

# Default client handles (for callers that want to seed/inspect tenant data).
client = get_saas_client()
default_client = client
troy_client = client

__all__ = [
    "router",
    "saas_router",
    "create_saas_router",
    "get_saas_client",
    "get_default_client",
    "get_client",
    "normalize_webhook_payload",
    "normalize_payload",
    "normalize_webhook",
    "SUPPORTED_PROVIDERS",
    "client",
    "default_client",
    "troy_client",
]
