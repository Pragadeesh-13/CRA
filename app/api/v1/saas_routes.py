"""
REST & webhook integration surfaces for Troy (open-source tool).

Troy is a clean, single-product open-source tool: one Troy client, one
product configuration, no registries to set up.

Mounted into the FastAPI app (see ``app/main.py``) at ``/api/v1/saas``:

  - ``POST /api/v1/saas/resolve``                    — standard JSON resolution
  - ``POST /api/v1/saas/webhooks/{provider}``        — Zendesk / Intercom /
    Stripe / Freshdesk / Shopify / Slack / generic webhooks, normalised
    then resolved
  - ``GET  /api/v1/saas/info``                       — product & capability metadata

The module-level ``router`` uses a lazily-created default
:class:`~app.sdk.TroyClient`. Use :func:`create_saas_router` to mount the
same routes against a custom (e.g. seeded) client.

See ``docs/MODULES.md`` for the full module reference.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.auth.session import has_webhook_relay_token
from app.config import settings
from app.http.execution_flags import execution_flag
from app.http.request_json import (
    COMPLAINT_FIELDS, ID_TYPES, EnvelopeError, check_field, envelope_error,
    read_field, read_json_object, resolution_fields,
)
from app.sdk.client import TroyClient, _jsonable

SUPPORTED_PROVIDERS = ["zendesk", "intercom", "stripe", "freshdesk", "shopify", "slack", "generic"]

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _webhook_text(data: dict, *keys: str) -> Optional[str]:
    for key in ("message", "comment"):
        nested = data.get(key)
        if key in keys and isinstance(nested, dict):
            data = {**data, key: read_field(nested, "text", "body", "description", kind="text", types=(str,))}
    return read_field(data, *keys, kind="text", types=(str,))


def _webhook_id(data: dict, *keys: str) -> Optional[str | int]:
    return read_field(data, *keys, kind="text or an integer", types=ID_TYPES)


def _event_label(data: dict, *keys: str) -> Optional[str]:
    if "event" in keys and isinstance(data.get("event"), dict):
        data = {**data, "event": data["event"].get("type")}
    return read_field(data, *keys, kind="text", types=(str,))


def _webhook_replay_key(provider: str, request: Request, body: dict) -> Optional[str]:
    """Use delivery identity only after the trusted relay authenticates."""
    if provider not in SUPPORTED_PROVIDERS:
        return None
    event_id = request.headers.get("X-Troy-Webhook-Event-Id")
    if event_id is None:
        if provider == "stripe" and body.get("object") == "event":
            event_id = body.get("id")
        elif provider == "slack" and body.get("type") == "event_callback":
            event_id = body.get("event_id")
        elif provider == "intercom" and body.get("type") == "notification_event":
            event_id = body.get("id")
        elif provider == "shopify":
            event_id = request.headers.get("X-Shopify-Event-Id")
    if event_id is None:
        return None
    check_field(event_id, "webhook event ID", "text", (str,))
    event_id = event_id.strip()
    if not event_id or len(event_id) > 255 or any(not 33 <= ord(char) <= 126 for char in event_id):
        raise EnvelopeError("Webhook event ID must be a nonempty delivery identifier of at most 255 characters.")
    return f"webhook:{provider}:{event_id}"


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
        ticket = check_field(data.get("ticket"), "ticket", "an object", (dict,))
        if ticket is None:
            ticket = data
        requester = ticket.get("requester") if isinstance(ticket.get("requester"), dict) else data.get("requester") or {}
        if not isinstance(requester, dict):
            requester = {}
        subject = _webhook_text(ticket, "subject", "title") or _webhook_text(data, "subject", "title")
        complaint = _webhook_text(ticket, "description", "comment", "body", "complaint", "query", "text") or _webhook_text(data, "description", "comment", "body", "complaint")
        customer_id = _webhook_id(ticket, "customer_id", "requester_id", "requesterId") or _webhook_id(requester, "id", "customer_id")
        customer_email = _first(requester, "email", "customer_email") or _first(ticket, "email", "customer_email")
        customer_name = _first(requester, "name", "customer_name") or _first(ticket, "name", "customer_name")
        order_id = _webhook_id(ticket, "order_id", "orderId") or _webhook_id(data, "order_id")
        event_type = _event_label(data, "type", "event", "event_type") or _first(ticket, "status")
        extra = {"ticket_id": _first(ticket, "id", "ticket_id")}

    elif provider_name == "intercom":
        item = _dig(data, "data.item", "data.conversation", "conversation", "ticket", "data") or {}
        if not isinstance(item, dict):
            item = {}
        user = _dig(data, "data.user", "user", "contact", "customer") or {}
        if not isinstance(user, dict):
            user = {}
        subject = _webhook_text(item, "title", "subject", "name") or _webhook_text(data, "subject", "title")
        complaint = (
            _webhook_text(item, "body", "description", "complaint", "query", "text", "message")
            or _webhook_text(data, "body", "description", "complaint", "query", "text", "message")
        )
        customer_id = _webhook_id(item, "customer_id") or _webhook_id(user, "id", "customer_id", "external_id")
        customer_email = _first(user, "email", "customer_email") or _first(item, "email")
        customer_name = _first(user, "name", "customer_name") or _first(item, "name")
        order_id = _webhook_id(item, "order_id", "orderId") or _webhook_id(data, "order_id")
        event_type = _event_label(data, "type", "event", "event_type", "topic")
        extra = {"conversation_id": _first(item, "id", "conversation_id")}

    elif provider_name == "freshdesk":
        ticket = check_field(data.get("ticket"), "ticket", "an object", (dict,))
        if ticket is None:
            ticket = data
        requester = ticket.get("requester") if isinstance(ticket.get("requester"), dict) else data.get("requester") or {}
        if not isinstance(requester, dict):
            requester = {}
        subject = _webhook_text(ticket, "subject", "title") or _webhook_text(data, "subject", "title")
        complaint = _webhook_text(ticket, "description", "body", "complaint", "query", "text") or _webhook_text(data, "description", "body", "complaint")
        customer_id = _webhook_id(ticket, "customer_id", "requester_id", "requesterId", "contact_id") or _webhook_id(requester, "id", "customer_id")
        customer_email = _first(requester, "email", "customer_email") or _first(ticket, "email", "customer_email")
        customer_name = _first(requester, "name", "customer_name") or _first(ticket, "name", "customer_name")
        order_id = _webhook_id(ticket, "order_id", "orderId") or _webhook_id(data, "order_id")
        event_type = _event_label(data, "type", "event", "event_type") or _first(ticket, "status")
        extra = {"ticket_id": _first(ticket, "id", "ticket_id")}

    elif provider_name == "stripe":
        event_data = check_field(data.get("data"), "data", "an object", (dict,)) or {}
        check_field(event_data.get("object"), "data.object", "an object", (dict,))
        obj = _dig(data, "data.object", "data", "object", "charge", "payment_intent", "invoice") or {}
        if not isinstance(obj, dict):
            obj = {}
        if not obj and data.get("object") != "event":
            obj = data
        for field in ("customer", "invoice"):
            if isinstance(obj.get(field), dict):
                reference = _webhook_id(obj[field], "id")
                if reference is None:
                    raise EnvelopeError(f"Stripe {field} object must include an ID.")
                obj = {**obj, field: reference}
        event_type = _event_label(data, "type", "event", "event_type") or _event_label(obj, "type", "status")
        metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        amount = read_field(obj, "amount", "amount_refunded", "total", kind="text or a number", types=(str, int, float))
        amount_str = ""
        if isinstance(amount, (int, float)) and amount:
            monetary_amount = Decimal(str(amount))
            amount_str = f" ${monetary_amount / 100 if amount >= 100 else monetary_amount:.2f}"
        description = _webhook_text(obj, "description", "reason", "statement_descriptor") or _webhook_text(data, "description")
        resource = _webhook_id(obj, "id", "invoice", "customer")
        subject = f"Stripe event {event_type or 'charge'}" if resource is not None else None
        if description or resource is not None:
            complaint = (
                f"Stripe webhook {event_type or 'event'}: {description or 'charge event'}"
                f"{amount_str} for customer {obj.get('customer') or ''}".strip()
            )
            if "refund" in (event_type or "").lower() or "refund" in (description or "").lower():
                complaint = f"Customer was charged twice and requests a refund. {complaint}"
        customer_id = _webhook_id(metadata, "customer_id", "customerId") or _webhook_id(obj, "customer", "customer_id")
        customer_email = _first(obj, "receipt_email", "email", "customer_email") or _first(metadata, "customer_email", "email")
        order_id = _webhook_id(metadata, "order_id", "orderId") or _webhook_id(obj, "order_id", "invoice")
        transaction_id = _webhook_id(metadata, "transaction_id", "transactionId") or _webhook_id(obj, "id", "transaction_id")
        # Stripe ids (ch_..., pi_...) are not TXN- ids; only keep them as the
        # transaction reference when no TXN-style id is present.
        extra = {"stripe_event_id": _first(data, "id", "event_id"), "amount": amount, "currency": obj.get("currency")}

    elif provider_name == "shopify":
        # Shopify order / webhook payloads. The order object may be the
        # top-level payload or nested under ``order`` / ``data``.
        order = data.get("order") if isinstance(data.get("order"), dict) else _dig(data, "data.order", "data.object") or data
        if not isinstance(order, dict):
            order = {}
        customer = order.get("customer") if isinstance(order.get("customer"), dict) else data.get("customer") or {}
        if not isinstance(customer, dict):
            customer = {}
        # Complaint: note / cancel_reason / reason (plus generic fallbacks).
        complaint = (
            _webhook_text(order, "note", "cancel_reason", "cancelReason", "reason", "description", "body", "complaint", "query", "text")
            or _webhook_text(data, "note", "cancel_reason", "cancelReason", "reason", "description", "body", "complaint", "query", "text")
            or _dig(data, "order.note", "order.cancel_reason", "data.note", "data.cancel_reason")
        )
        # Order id: name ("#1001") / order_number / order_id / id.
        order_id = (
            _webhook_id(order, "name", "order_number", "order_id", "orderId", "id")
            or _webhook_id(data, "name", "order_number", "order_id", "orderId")
        )
        customer_id = (
            _webhook_id(customer, "id", "customer_id", "customerId")
            or _webhook_id(order, "customer_id", "customerId")
            or _webhook_id(data, "customer_id", "customerId")
        )
        customer_email = (
            _first(customer, "email", "customer_email")
            or _first(order, "email", "customer_email")
            or _first(data, "customer_email", "email")
        )
        customer_name = _first(customer, "customer_name", "name")
        if not customer_name:
            first_name = _first(customer, "first_name", "firstName")
            last_name = _first(customer, "last_name", "lastName")
            parts = [str(p).strip() for p in (first_name, last_name) if p and str(p).strip()]
            customer_name = " ".join(parts) if parts else None
        customer_name = (
            customer_name
            or _first(order, "customer_name", "name")
            or _first(data, "customer_name", "name")
        )
        subject = (
            _webhook_text(order, "title", "subject")
            or _webhook_text(data, "title", "subject")
            or (f"Shopify order {order_id}" if order_id else None)
        )
        event_type = (
            _first(data, "type", "event", "event_type", "topic", "action")
            or _first(order, "financial_status", "fulfillment_status", "cancel_reason")
        )
        extra = {
            "order_number": _webhook_id(order, "order_number") or _webhook_id(data, "order_number"),
            "shopify_order_id": _first(order, "id") or _first(data, "id"),
            "financial_status": _first(order, "financial_status"),
            "cancel_reason": _webhook_text(order, "cancel_reason", "cancelReason") or _webhook_text(data, "cancel_reason", "cancelReason"),
        }

    elif provider_name == "slack":
        # Slack message / event payloads: text carries the complaint and
        # user_id / user_name identify the sender.
        message = _dig(data, "event", "message", "data", "data.message") or {}
        if not isinstance(message, dict):
            message = {}
        # Some payloads nest the user as a dict under ``user``.
        user_obj = message.get("user") if isinstance(message.get("user"), dict) else data.get("user") if isinstance(data.get("user"), dict) else {}
        if not isinstance(user_obj, dict):
            user_obj = {}
        complaint = (
            _webhook_text(data, "text", "message", "complaint", "body", "query", "description")
            or _webhook_text(message, "text", "message", "complaint", "body")
        )
        customer_id = (
            _webhook_id(data, "user_id", "userId", "customer_id", "customerId")
            or _webhook_id(message, "user_id", "userId", "customer_id", "customerId")
            or _webhook_id(user_obj, "id", "customer_id", "user_id")
        )
        # ``user`` may itself be the id string.
        if customer_id is None:
            raw_user = _first(data, "user") or _first(message, "user")
            if isinstance(raw_user, str) and raw_user.strip():
                customer_id = raw_user
        customer_name = (
            _first(data, "user_name", "userName", "username", "customer_name", "name")
            or _first(message, "user_name", "userName", "username", "customer_name", "name")
            or _first(user_obj, "name", "user_name", "username", "customer_name")
        )
        customer_email = (
            _first(data, "customer_email", "email")
            or _first(message, "customer_email", "email")
            or _first(user_obj, "email", "customer_email")
        )
        order_id = _webhook_id(data, "order_id", "orderId") or _webhook_id(message, "order_id", "orderId")
        transaction_id = _webhook_id(data, "transaction_id", "transactionId") or _webhook_id(message, "transaction_id", "transactionId")
        subject = _webhook_text(data, "subject", "title") or _webhook_text(message, "subject", "title")
        event_type = _event_label(data, "type", "event", "event_type", "topic", "action") or _first(message, "type", "event_type")
        extra = {
            "channel": _first(data, "channel", "channel_id", "channelId") or _first(message, "channel", "channel_id"),
            "user_id": customer_id,
            "user_name": customer_name,
            "team_id": _first(data, "team_id", "teamId") or _first(message, "team_id"),
        }

    # Generic (and fallback for every provider).
    if complaint is None:
        complaint = _webhook_text(
            data,
            "complaint", "query", "text", "message", "description", "body", "content", "issue",
            "note", "cancel_reason", "cancelReason", "reason",
        ) or _dig(data, "ticket.description", "ticket.body", "ticket.complaint", "conversation.body", "data.item.body", "data.object.description", "order.note", "order.cancel_reason", "event.text", "message.text")
    if subject is None:
        subject = _webhook_text(data, "subject", "title") or _dig(data, "ticket.subject", "ticket.title", "data.item.title")
    if customer_id is None:
        customer_id = _webhook_id(data, "customer_id", "customerId", "user_id", "userId", "requester_id") or _dig(data, "customer.id", "user.id", "user_id", "requester.id", "ticket.requester_id", "event.user", "message.user")
        if isinstance(customer_id, dict):
            customer_id = _first(customer_id, "id", "customer_id", "user_id")
    if customer_email is None:
        customer_email = _first(data, "customer_email", "email") or _dig(data, "customer.email", "user.email", "requester.email")
    if customer_name is None:
        customer_name = _first(data, "customer_name", "name", "user_name", "userName", "username") or _dig(data, "customer.name", "user.name", "user_name", "requester.name", "customer.first_name")
    if order_id is None:
        order_id = _webhook_id(data, "order_id", "orderId", "order_number") or _dig(data, "customer.order_id", "ticket.order_id", "data.object.metadata.order_id", "order.name", "order.order_number", "order.order_id")
    if transaction_id is None:
        transaction_id = _webhook_id(data, "transaction_id", "transactionId", "txn_id") or _dig(data, "ticket.transaction_id", "data.object.metadata.transaction_id")
    if event_type is None:
        event_type = _event_label(data, "type", "event", "event_type", "topic", "action")

    check_field(complaint, "webhook complaint", "text", (str,))
    check_field(subject, "webhook subject", "text", (str,))
    for field, value in (("customer_id", customer_id), ("order_id", order_id), ("transaction_id", transaction_id)):
        check_field(value, field, "text or an integer", ID_TYPES)
    for field, value in (("customer_email", customer_email), ("customer_name", customer_name)):
        check_field(value, field, "text", (str,))
    # Some provider status codes (e.g. Freshdesk ticket status) are integers.
    check_field(event_type, "event_type", "text or an integer", ID_TYPES)

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


def _client_for_body(body: Dict[str, Any], base_client: TroyClient) -> TroyClient:
    """Honour a per-request ``config`` override while sharing data."""
    config = body.get("config") if isinstance(body, dict) else None
    if config is None:
        return base_client
    allowed = {"product_name", "support_email", "domain", "branding"}
    if not isinstance(config, dict) or set(config) - allowed:
        raise HTTPException(400, "Request config supports product_name, support_email, domain and branding")
    try:
        return TroyClient(
            config={**base_client.config.model_dump(), **config},
            adapter=base_client.adapter,
            tool_registry=base_client.tool_registry,
            driver=base_client.driver,
            review_queue=base_client.review_queue,
        )
    except ValueError as exc:
        raise HTTPException(400, "Invalid request branding configuration") from exc


def _info_payload(client: TroyClient) -> Dict[str, Any]:
    info = client.get_info()
    return {
        **info,
        "ok": True,
        "product_name": client.config.product_name,
        "product": info.get("product"),
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


def create_saas_router(client: Optional[TroyClient] = None, **_ignored: Any) -> APIRouter:
    """
    Build the SaaS router. With ``client`` the routes close over it;
    without one they resolve the shared default client via dependency
    injection (so tests can override ``get_saas_client``).

    Any extra keyword arguments are accepted and ignored, for
    backwards compatibility with older callers.
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
        troy_client = _client_for_body(body, _resolve_client(dep_client))
        return await troy_client.resolve_ticket(
            complaint,
            **options,
            auto_execute=execution_flag(body, "auto_execute", "autoExecute", True),
            dry_run=execution_flag(body, "dry_run", "dryRun", False),
        )

    @router.post("/webhooks/{provider}")
    async def saas_webhook(provider: str, request: Request, dep_client: TroyClient = Depends(get_saas_client)):
        verified_relay = has_webhook_relay_token(request)
        if settings.webhook_token and not verified_relay:
            raise HTTPException(401, "Webhook relay authentication required")
        body = await read_json_object(request)
        if body is None:
            return envelope_error()
        try:
            options = resolution_fields(body, request.headers.get("Idempotency-Key"))
            normalized = normalize_webhook_payload(provider, body)
            delivery_key = _webhook_replay_key(normalized["provider"], request, body) if verified_relay else None
        except EnvelopeError as exc:
            return envelope_error(str(exc))
        complaint = normalized.get("complaint") or ""
        if not str(complaint).strip():
            return JSONResponse(
                status_code=400,
                content={"ok": False, "status": "Failed", "provider": provider, "error": "Could not extract a complaint from the webhook payload.", "normalized": normalized},
            )
        troy_client = _client_for_body(body, _resolve_client(dep_client))
        context = dict(normalized.get("context") or {})
        context.update(options["context"] or {})
        idempotency_key = options["idempotency_key"]
        idempotency_key = idempotency_key or delivery_key
        result = await troy_client.resolve_ticket(
            str(complaint),
            customer_id=normalized.get("customer_id"),
            order_id=normalized.get("order_id"),
            context=context,
            transaction_id=options["transaction_id"] if options["transaction_id"] is not None else normalized.get("transaction_id"),
            idempotency_key=idempotency_key,
            _verified_delivery_key=delivery_key,
            auto_execute=execution_flag(body, "auto_execute", "autoExecute", True),
            dry_run=execution_flag(body, "dry_run", "dryRun", False),
        )
        response = {
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
        if result.get("status") in {"delivery_in_progress", "action_in_progress"}:
            return JSONResponse(status_code=409, headers={"Retry-After": "1"}, content=response)
        if result.get("status") == "delivery_requires_reconciliation":
            return JSONResponse(status_code=409, content=response)
        return response

    return router


# Module-level router mounted by app/main.py.
router = create_saas_router()
saas_router = router

# Default client handles (for callers that want to seed/inspect data).
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
