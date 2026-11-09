"""
SaaS REST & webhook integration surfaces for Troy.

Mounted into the FastAPI app (see ``app/main.py``) at ``/api/v1/saas``:

  - ``POST /api/v1/saas/resolve``                    — standard JSON resolution
  - ``POST /api/v1/saas/webhooks/{provider}``        — Zendesk / Intercom /
    Stripe / Freshdesk / Shopify / Slack / generic webhooks, normalised
    then resolved
  - ``GET  /api/v1/saas/info``                       — tenant & capability metadata
  - ``POST /api/v1/saas/tenants``                    — register/update a tenant
  - ``GET  /api/v1/saas/tenants``                    — list registered tenants
  - ``GET  /api/v1/saas/tenants/{tenant_id}``        — tenant info & capabilities

The module-level ``router`` uses a lazily-created default
:class:`~app.sdk.TroyClient`. Use :func:`create_saas_router` to mount the
same routes against a custom (e.g. seeded) client.

Multi-tenancy: a :class:`TenantRegistry` stores one :class:`TroyClient`
per ``tenant_id``. Requests to ``/resolve``, ``/info`` and
``/webhooks/{provider}`` resolve the tenant dynamically via the
``X-Tenant-ID`` header, the ``?tenant=`` query parameter, or a
``tenant_id`` / ``tenantId`` field in the payload. Unregistered / absent
tenant ids fall back to the default (or bound) client, so single-tenant
usage is unchanged.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.sdk.client import TroyClient, _jsonable

logger = logging.getLogger(__name__)

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
# Multi-tenant registry
# ---------------------------------------------------------------------------


class TenantRegistry:
    """
    Store and retrieve :class:`TroyClient` instances by ``tenant_id``.

    Each registered tenant gets its own ``TroyClient`` built from the
    supplied ``config`` (a :class:`~app.core.saas_config.SaaSConfig`,
    dict, JSON/YAML string, or path).  A fresh ``TroyClient`` therefore
    has its own adapter / tool registry / driver unless a pre-built
    ``client`` is passed to :meth:`register`, which keeps tenant data
    isolated by default.

    The registry is intentionally permissive about the shapes callers
    use: ``register("acme", {"product_name": "Acme"})``,
    ``register(tenant_id="acme", config={...})`` and
    ``register({"tenant_id": "acme", "config": {...}})`` are all accepted.
    """

    def __init__(self, default_client: Optional[TroyClient] = None, **kwargs: Any) -> None:
        # Accept a few alias spellings for the fallback client.
        if default_client is None:
            default_client = kwargs.pop("base_client", None) or kwargs.pop("default", None) or kwargs.pop("client", None)
        self._default_client = default_client
        self._clients: Dict[str, TroyClient] = {}
        self._profiles: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    # -- registration -----------------------------------------------------

    def register(
        self,
        tenant_id: Any = None,
        config: Any = None,
        client: Optional[TroyClient] = None,
        **kwargs: Any,
    ) -> TroyClient:
        """Register (or update) a tenant and return its ``TroyClient``."""
        # Dict-profile form: register({"tenant_id": ..., "config": {...}})
        if isinstance(tenant_id, dict):
            profile = dict(tenant_id)
            tenant_id = _first(profile, "tenant_id", "tenantId", "tenant", "id")
            if config is None:
                config = profile.get("config")
            if client is None and isinstance(profile.get("client"), TroyClient):
                client = profile["client"]
            # Top-level config-ish fields on the profile act as config.
            if isinstance(profile.get("config"), dict):
                merged: Dict[str, Any] = dict(profile["config"])
            elif config is None:
                merged = {}
            else:
                merged = {}
            if isinstance(config, dict):
                merged.update(config)
            for key in ("product_name", "name", "product", "domain", "support_email", "email", "branding", "categories", "policies", "entity_patterns"):
                if key in profile and profile[key] is not None:
                    merged[key] = profile[key]
            config = merged or config
        if tenant_id is None:
            tenant_id = kwargs.pop("tenantId", None) or kwargs.pop("tenant", None) or kwargs.pop("id", None)
        tid = str(tenant_id).strip() if tenant_id is not None else ""
        if not tid:
            raise ValueError("tenant_id is required to register a tenant.")
        if config is None:
            config = kwargs.pop("tenant_config", None) or kwargs.pop("saas_config", None)
        if client is None and isinstance(kwargs.get("troy_client"), TroyClient):
            client = kwargs.pop("troy_client")
        if client is None:
            # Build a tenant-isolated client.  When config is absent we
            # still honour any product_name supplied as a kwarg.
            effective_config = config
            if effective_config is None and kwargs:
                effective_config = {k: v for k, v in kwargs.items() if not k.startswith("_")}
            elif isinstance(effective_config, dict) and kwargs:
                effective_config = {**effective_config, **{k: v for k, v in kwargs.items() if not k.startswith("_")}}
            try:
                client = TroyClient(config=effective_config) if effective_config is not None else TroyClient()
            except Exception as exc:
                logger.warning("Falling back to default config for tenant %s: %s", tid, exc)
                client = TroyClient()
        with self._lock:
            self._clients[tid] = client
            self._profiles[tid] = {
                "tenant_id": tid,
                "product_name": getattr(getattr(client, "config", None), "product_name", None) or tid,
                "config": config,
                "client": client,
            }
        return client

    # Aliases
    register_tenant = register
    add = register
    add_tenant = register
    upsert = register
    set = register

    def unregister(self, tenant_id: Any) -> bool:
        tid = str(tenant_id).strip() if tenant_id is not None else ""
        with self._lock:
            existed = tid in self._clients
            self._clients.pop(tid, None)
            self._profiles.pop(tid, None)
            return existed

    remove = unregister
    delete = unregister
    delete_tenant = unregister

    def clear(self) -> None:
        with self._lock:
            self._clients.clear()
            self._profiles.clear()

    # -- retrieval --------------------------------------------------------

    def get(self, tenant_id: Any = None, default: Any = None) -> Optional[TroyClient]:
        if tenant_id is None:
            return default
        tid = str(tenant_id).strip()
        if not tid:
            return default
        with self._lock:
            return self._clients.get(tid, default)

    def get_client(self, tenant_id: Any = None, default: Any = None) -> Optional[TroyClient]:
        return self.get(tenant_id, default)

    def get_tenant(self, tenant_id: Any = None, default: Any = None) -> Optional[TroyClient]:
        return self.get(tenant_id, default)

    def resolve(self, tenant_id: Any = None, fallback: Optional[TroyClient] = None) -> TroyClient:
        """Return the tenant client for ``tenant_id`` or ``fallback``."""
        client = self.get(tenant_id)
        if client is not None:
            return client
        if fallback is not None:
            return fallback
        if self._default_client is not None:
            return self._default_client
        return get_saas_client()

    def profile(self, tenant_id: Any) -> Optional[Dict[str, Any]]:
        tid = str(tenant_id).strip() if tenant_id is not None else ""
        with self._lock:
            entry = self._profiles.get(tid)
            return dict(entry) if entry is not None else None

    def info(self, tenant_id: Any) -> Optional[Dict[str, Any]]:
        client = self.get(tenant_id)
        if client is None:
            return None
        tid = str(tenant_id).strip()
        payload = _info_payload(client)
        payload["tenant_id"] = tid
        payload["tenant"] = payload.get("tenant") or {}
        if isinstance(payload["tenant"], dict):
            payload["tenant"] = {**payload["tenant"], "tenant_id": tid}
        payload["ok"] = True
        return payload

    def list_tenants(self) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._profiles.items())
        return [
            {"tenant_id": tid, "product_name": profile.get("product_name")}
            for tid, profile in sorted(items, key=lambda kv: kv[0])
        ]

    # Aliases / conveniences
    list = list_tenants
    tenants = list_tenants

    def list_ids(self) -> List[str]:
        return [entry["tenant_id"] for entry in self.list_tenants()]

    def __contains__(self, tenant_id: Any) -> bool:
        return self.get(tenant_id) is not None

    def __bool__(self) -> bool:
        # An empty registry is still a valid (truthy) registry object;
        # without this, ``registry or default`` would discard it.
        return True

    def __len__(self) -> int:
        with self._lock:
            return len(self._clients)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"TenantRegistry(tenants={self.list_ids()!r})"


# Module-level shared registry used by the default mounted router.
tenant_registry = TenantRegistry()
registry = tenant_registry


def get_tenant_registry() -> TenantRegistry:
    """Return the shared module-level :class:`TenantRegistry`."""
    return tenant_registry


def _tenant_id_from_request(request: Optional[Request], body: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Resolve a tenant id from header, query param, or payload field."""
    if request is not None:
        # Header: X-Tenant-ID (header lookup is case-insensitive in Starlette)
        try:
            header_value = request.headers.get("x-tenant-id") or request.headers.get("X-Tenant-ID")
        except Exception:
            header_value = None
        if header_value and str(header_value).strip():
            return str(header_value).strip()
        # Query parameter: ?tenant=... (also accept tenant_id / tenantId)
        try:
            params = request.query_params
            for key in ("tenant", "tenant_id", "tenantId"):
                value = params.get(key)
                if value and str(value).strip():
                    return str(value).strip()
        except Exception:
            pass
    if isinstance(body, dict):
        for key in ("tenant_id", "tenantId"):
            value = body.get(key)
            if value and str(value).strip():
                return str(value).strip()
        # Nested tenant hints (context / data) — only explicit id fields.
        for nested_key in ("context", "data"):
            nested = body.get(nested_key)
            if isinstance(nested, dict):
                for key in ("tenant_id", "tenantId"):
                    value = nested.get(key)
                    if value and str(value).strip():
                        return str(value).strip()
    return None


def _resolve_tenant_client(
    request: Optional[Request],
    body: Optional[Dict[str, Any]],
    base_client: TroyClient,
    reg: Optional[TenantRegistry] = None,
) -> TroyClient:
    """Return the registered tenant client for this request, else ``base_client``."""
    registry_obj = reg if reg is not None else tenant_registry
    tid = _tenant_id_from_request(request, body)
    if tid:
        tenant_client = registry_obj.get(tid)
        if tenant_client is not None:
            return tenant_client
    return base_client


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
            _first(order, "note", "cancel_reason", "cancelReason", "reason", "description", "body", "complaint", "query", "text")
            or _first(data, "note", "cancel_reason", "cancelReason", "reason", "description", "body", "complaint", "query", "text")
            or _dig(data, "order.note", "order.cancel_reason", "data.note", "data.cancel_reason")
        )
        # Order id: name ("#1001") / order_number / order_id / id.
        order_id = (
            _first(order, "name", "order_number", "order_id", "orderId", "id")
            or _first(data, "name", "order_number", "order_id", "orderId")
        )
        customer_id = (
            _first(customer, "id", "customer_id", "customerId")
            or _first(order, "customer_id", "customerId")
            or _first(data, "customer_id", "customerId")
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
            _first(order, "title", "subject")
            or _first(data, "title", "subject")
            or (f"Shopify order {order_id}" if order_id else None)
        )
        event_type = (
            _first(data, "type", "event", "event_type", "topic", "action")
            or _first(order, "financial_status", "fulfillment_status", "cancel_reason")
        )
        extra = {
            "order_number": _first(order, "order_number") or _first(data, "order_number"),
            "shopify_order_id": _first(order, "id") or _first(data, "id"),
            "financial_status": _first(order, "financial_status"),
            "cancel_reason": _first(order, "cancel_reason", "cancelReason") or _first(data, "cancel_reason", "cancelReason"),
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
            _first(data, "text", "message", "complaint", "body", "query", "description")
            or _first(message, "text", "message", "complaint", "body")
        )
        customer_id = (
            _first(data, "user_id", "userId", "customer_id", "customerId")
            or _first(message, "user_id", "userId", "customer_id", "customerId")
            or _first(user_obj, "id", "customer_id", "user_id")
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
        order_id = _first(data, "order_id", "orderId") or _first(message, "order_id", "orderId")
        transaction_id = _first(data, "transaction_id", "transactionId") or _first(message, "transaction_id", "transactionId")
        subject = _first(data, "subject", "title") or _first(message, "subject", "title")
        event_type = _first(data, "type", "event", "event_type", "topic", "action") or _first(message, "type", "event_type")
        extra = {
            "channel": _first(data, "channel", "channel_id", "channelId") or _first(message, "channel", "channel_id"),
            "user_id": customer_id,
            "user_name": customer_name,
            "team_id": _first(data, "team_id", "teamId") or _first(message, "team_id"),
        }

    # Generic (and fallback for every provider).
    if complaint is None:
        complaint = _first(
            data,
            "complaint", "query", "text", "message", "description", "body", "content", "issue",
            "note", "cancel_reason", "cancelReason", "reason",
        ) or _dig(data, "ticket.description", "ticket.body", "ticket.complaint", "conversation.body", "data.item.body", "data.object.description", "order.note", "order.cancel_reason", "event.text", "message.text")
    if subject is None:
        subject = _first(data, "subject", "title") or _dig(data, "ticket.subject", "ticket.title", "data.item.title")
    if customer_id is None:
        customer_id = _first(data, "customer_id", "customerId", "user_id", "userId", "requester_id") or _dig(data, "customer.id", "user.id", "user_id", "requester.id", "ticket.requester_id", "event.user", "message.user")
        if isinstance(customer_id, dict):
            customer_id = _first(customer_id, "id", "customer_id", "user_id")
    if customer_email is None:
        customer_email = _first(data, "customer_email", "email") or _dig(data, "customer.email", "user.email", "requester.email")
    if customer_name is None:
        customer_name = _first(data, "customer_name", "name", "user_name", "userName", "username") or _dig(data, "customer.name", "user.name", "user_name", "requester.name", "customer.first_name")
    if order_id is None:
        order_id = _first(data, "order_id", "orderId", "order_number") or _dig(data, "customer.order_id", "ticket.order_id", "data.object.metadata.order_id", "order.name", "order.order_number", "order.order_id")
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
            "POST /api/v1/saas/tenants",
            "GET /api/v1/saas/tenants",
            "GET /api/v1/saas/tenants/{tenant_id}",
        ],
        "providers": SUPPORTED_PROVIDERS,
        "supported_providers": SUPPORTED_PROVIDERS,
    }


def create_saas_router(
    client: Optional[TroyClient] = None,
    tenant_registry: Optional[TenantRegistry] = None,
    registry: Optional[TenantRegistry] = None,
    **kwargs: Any,
) -> APIRouter:
    """
    Build the SaaS router. With ``client`` the routes close over it;
    without one they resolve the shared default client via dependency
    injection (so tests can override ``get_saas_client``).

    ``tenant_registry`` (alias ``registry``) optionally supplies the
    :class:`TenantRegistry` used for multi-tenant resolution; by default
    the module-level shared registry is used, so tenants registered via
    the mounted app are visible through :func:`get_tenant_registry`.
    """
    router = APIRouter(prefix="/api/v1/saas", tags=["saas"])
    bound_client = client
    _explicit_registry = tenant_registry if tenant_registry is not None else (registry if registry is not None else kwargs.pop("tenant_registry_obj", None))
    shared_registry = _explicit_registry if _explicit_registry is not None else (globals().get("tenant_registry") or TenantRegistry())
    # Keep a handle on the router for callers that want to inspect it.
    router.tenant_registry = shared_registry  # type: ignore[attr-defined]

    def _resolve_client(dep_client: Optional[TroyClient] = None) -> TroyClient:
        # A bound client wins; otherwise honour FastAPI dependency injection
        # (get_saas_client) so app.dependency_overrides works as expected.
        return bound_client if bound_client is not None else (dep_client or get_saas_client())

    def _tenant_client(request: Request, body: Optional[Dict[str, Any]], dep_client: Optional[TroyClient] = None) -> TroyClient:
        base = _resolve_client(dep_client)
        tenant_client = _resolve_tenant_client(request, body, base, shared_registry)
        # A per-request ``config`` override still wins over the tenant client.
        return _client_for_body(body or {}, tenant_client)

    @router.get("/info")
    async def saas_info(request: Request, dep_client: TroyClient = Depends(get_saas_client)) -> Dict[str, Any]:
        resolved = _tenant_client(request, None, dep_client)
        payload = _info_payload(resolved)
        tid = _tenant_id_from_request(request, None)
        if tid:
            payload["tenant_id"] = tid
            if isinstance(payload.get("tenant"), dict):
                payload["tenant"] = {**payload["tenant"], "tenant_id": tid}
        return payload

    @router.post("/resolve")
    async def saas_resolve(request: Request, dep_client: TroyClient = Depends(get_saas_client)):
        body = await _json_body(request)
        complaint = _first(body, "complaint", "query", "text", "message", "ticket", "description", "body")
        if not complaint or not str(complaint).strip():
            return JSONResponse(status_code=400, content={"ok": False, "status": "Failed", "error": "Complaint text is required."})
        troy_client = _tenant_client(request, body, dep_client)
        tid = _tenant_id_from_request(request, body)
        context = body.get("context") if isinstance(body.get("context"), dict) else None
        result = await troy_client.resolve_ticket(
            str(complaint),
            customer_id=_first(body, "customer_id", "customerId"),
            order_id=_first(body, "order_id", "orderId"),
            context=context,
            auto_execute=bool(body.get("auto_execute", body.get("autoExecute", True))),
            dry_run=bool(body.get("dry_run", body.get("dryRun", False))),
        )
        if tid and isinstance(result, dict):
            result = {**result, "tenant_id": tid}
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
        troy_client = _tenant_client(request, body, dep_client)
        tid = _tenant_id_from_request(request, body)
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
            **({"tenant_id": tid} if tid else {}),
        }

    # -- Multi-tenant registry endpoints --------------------------------

    @router.post("/tenants")
    async def saas_register_tenant(request: Request, dep_client: TroyClient = Depends(get_saas_client)):
        body = await _json_body(request)
        tenant_id = _first(body, "tenant_id", "tenantId", "tenant", "id")
        if not tenant_id or not str(tenant_id).strip():
            return JSONResponse(status_code=400, content={"ok": False, "error": "tenant_id is required."})
        tenant_id = str(tenant_id).strip()
        # Effective config: explicit ``config`` plus top-level config-ish fields.
        effective_config: Dict[str, Any] = {}
        raw_config = body.get("config")
        if isinstance(raw_config, dict):
            effective_config.update(raw_config)
        elif raw_config is not None:
            effective_config = raw_config  # string / SaaSConfig handled by TroyClient
        if isinstance(effective_config, dict):
            for key in ("product_name", "name", "product", "domain", "support_email", "email", "branding", "categories", "policies", "entity_patterns"):
                if key in body and body[key] is not None:
                    effective_config[key] = body[key]
            profile = body.get("profile")
            if isinstance(profile, dict):
                for key, value in profile.items():
                    effective_config.setdefault(key, value)
        tenant_client = shared_registry.register(tenant_id, config=effective_config if effective_config else None)
        payload = _info_payload(tenant_client)
        return {
            "ok": True,
            "tenant_id": tenant_id,
            "product_name": tenant_client.config.product_name,
            "tenant": payload.get("tenant"),
            "capabilities": payload.get("capabilities"),
            "info": payload,
        }

    @router.get("/tenants")
    async def saas_list_tenants():
        return shared_registry.list_tenants()

    @router.get("/tenants/{tenant_id}")
    async def saas_get_tenant(tenant_id: str):
        info = shared_registry.info(tenant_id)
        if info is None:
            return JSONResponse(status_code=404, content={"ok": False, "error": "Tenant not found.", "tenant_id": tenant_id})
        return info

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
    "TenantRegistry",
    "tenant_registry",
    "registry",
    "get_tenant_registry",
    "_tenant_id_from_request",
    "_resolve_tenant_client",
    "client",
    "default_client",
    "troy_client",
]
