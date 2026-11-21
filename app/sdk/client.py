"""
TroyClient — the plug-and-play SDK entry point for Troy.

A developer creates one client, optionally supplying their
:class:`~app.core.saas_config.SaaSConfig`, a data-store adapter, a tool
registry, and an LLM driver::

    from app.sdk import TroyClient
    from app.core.adapter import InMemoryDataAdapter

    client = TroyClient(
        config={"product_name": "MyApp", "domain": "example.com"},
        adapter=InMemoryDataAdapter(customers=[...], transactions=[...]),
    )
    result = await client.resolve_ticket(
        "I was charged twice for order ORD-88219 (USR-10492)",
    )

With no arguments the client is fully zero-setup: a default
``SaaSConfig``, an empty :class:`InMemoryDataAdapter`, a tool registry
pre-populated with standard refund/account tools, and an
``OpenAiDriver(provider='lmstudio')`` for the local LM Studio endpoint.
"""

from __future__ import annotations

import asyncio
import inspect
import hashlib
import json
import logging
import math
import os
import re
import threading
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Union

from app.core.adapter import DataStoreAdapter, InMemoryDataAdapter
from app.core.saas_config import SaaSConfig, load_saas_config
from app.core.tool_registry import TroyToolRegistry
from app.providers.jev import CATEGORY_DESCRIPTIONS, SDK_CATEGORY_ALIASES, classify_with_jev, configured_jev_model
from app.review_queue import REVIEW_DB_ENV, ReviewQueue, UnscopedDeliveryHold
from app.utils.time_utils import _utcnow_iso

logger = logging.getLogger(__name__)


def _jsonable(value: Any) -> Any:
    """Recursively convert a value into plain JSON-serialisable data."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "model_dump"):
        try:
            return _jsonable(value.model_dump(mode="json"))
        except Exception:
            return str(value)
    if hasattr(value, "to_dict"):
        try:
            return _jsonable(value.to_dict())
        except Exception:
            return str(value)
    return str(value)


def _first_str(*values: Any) -> Optional[str]:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _positive_amount(value: Any) -> Optional[float]:
    try:
        amount = float(str(value).strip())
    except (TypeError, ValueError, OverflowError):
        return None
    return amount if math.isfinite(amount) and amount > 0 else None


def _same_id(left: Any, right: Any) -> bool:
    return bool(left and right and str(left).strip().casefold() == str(right).strip().casefold())


def _refund_evidence(transactions, transaction_id):
    """Return the unique matching charge and whether it can be reversed."""
    if transaction_id and sum(_same_id(row.get("transaction_id"), transaction_id) for row in transactions) != 1:
        return None, False, False
    groups = {}
    for row in transactions:
        owner = _first_str(row.get("customer_id"))
        order = _first_str(row.get("order_id"), row.get("reference_order_id"))
        if owner and order and row.get("transaction_id"):
            groups.setdefault((owner.casefold(), order.casefold()), []).append(row)

    matches = []
    for rows in groups.values():
        if transaction_id and not any(_same_id(row["transaction_id"], transaction_id) for row in rows):
            continue
        settled = [row for row in rows if str(row.get("status")).casefold() == "settled"]
        refunded = [row for row in rows if str(row.get("status")).casefold() == "refunded"]
        if transaction_id:
            refunded = [row for row in refunded if _same_id(row["transaction_id"], transaction_id)]
        if len(refunded) == 1:
            recorded_cycle = str(refunded[0].get("billing_cycle") or "").strip().casefold()
            if transaction_id or (len(settled) <= 1 and all(
                str(row.get("billing_cycle") or "").strip().casefold() == recorded_cycle for row in settled
            )):
                matches.append((refunded[0], False, True))
                continue
        if len(settled) != 2 or any(str(row.get("status")).casefold() == "refunded" for row in rows):
            continue
        first, second = sorted(settled, key=lambda row: str(row.get("transaction_date") or ""))
        if transaction_id and not _same_id(second["transaction_id"], transaction_id):
            continue
        if (
            first.get("transaction_date") and second.get("transaction_date")
            and str(first["transaction_date"]) < str(second["transaction_date"])
            and not _same_id(first["transaction_id"], second["transaction_id"])
            and _positive_amount(second.get("amount")) is not None
            and _positive_amount(first.get("amount")) == _positive_amount(second.get("amount"))
            and str(first.get("currency") or "").strip().upper() == str(second.get("currency") or "").strip().upper()
            and str(first.get("billing_cycle") or "").strip().casefold() == str(second.get("billing_cycle") or "").strip().casefold()
        ):
            matches.append((second, True, False))
    return matches[0] if len(matches) == 1 else (None, False, False)


# Only these explicitly registered tools are eligible for business dispatch.
_BUSINESS_REQUESTS = (
    (r"\binvoice\b", r"\b(send|resend|email|generate|issue|need|request|provide|missing|never received|copy|give me)\b",
     "Billing", "send_invoice", "invoice delivery", "invoice_id", "invoices"),
    (r"\b(account|log.in|login|sign.in|2fa|mfa|otp|access|lockout|locked out)\b",
     r"\b(unlock|locked out|lockout|locked|cannot (?:log|sign) ?in|can['’]t (?:log|sign) ?in|(?:2fa|mfa|otp) (?:failed|not working))\b",
     "Authentication & Access", "unlock_account", "account unlock", None, None),
    (r"\b(subscription|proration|prorated|plan)\b", r"\b(credit|proration|prorated)\b",
     "Subscriptions", "apply_subscription_credit", "subscription credit", "subscription_id", "subscriptions"),
    (r"\bwebhooks?\b", r"\b(retry|replay|redeliver|resend|failed|timed out|timeouts?|not delivered|504)\b",
     "Integrations & APIs", "retry_webhook", "webhook retry", "event_id", "events"),
)


_INFORMATIONAL_QUESTION = re.compile(
    r"\s*(?:how (?:do|can|should|does|to)|what (?:is|are|does)|where (?:is|can|do)|"
    r"why (?:does|do)|can i|is there)\b", re.I,
)
_REFUND_REQUEST = re.compile(
    r"\b(?:refunds?|overcharged|unauthorized (?:charge|debit)|reverse (?:a |the )?charge|"
    r"duplicate (?:charge|debit|payment|bill|renewal)|double[ -](?:charge[ds]?|bill(?:ed|ing)?|debit|payment|renewal)|"
    r"(?:charged|billed|debited|paid) (?:me )?(?:twice|again))\b", re.I,
)


def _business_requests(complaint):
    if _INFORMATIONAL_QUESTION.match(complaint):
        return []
    return [rule[2:] for rule in _BUSINESS_REQUESTS
            if re.search(rule[0], complaint, re.I) and re.search(rule[1], complaint, re.I)]


def _business_parameters(business, customer, complaint, context, entities):
    _, tool, _, identifier, entity = business
    customer_id = _first_str((customer or {}).get("customer_id"))
    if not customer_id:
        return None
    params = {"customer_id": customer_id}
    if identifier:
        stored = customer.get(identifier)
        alternatives = customer.get(f"{identifier}s")
        verified = [stored] if stored else []
        if isinstance(alternatives, (list, tuple)):
            verified.extend(alternatives)
        verified = list(dict.fromkeys(str(item) for item in verified if isinstance(item, str) and item.strip()))
        extracted = entities.get(entity) or []
        if identifier == "event_id" and not extracted:
            extracted = re.findall(r"\b(?:evt|event)[_-][a-z0-9]+\b", complaint, re.I)
        supplied = context.get(identifier)
        wanted = _first_str(supplied, *extracted)
        if not wanted or any(not _same_id(item, wanted) for item in extracted):
            return None
        matching = [item for item in verified if _same_id(item, wanted)]
        if len(matching) != 1:
            return None
        params[identifier] = matching[0]
    if tool == "apply_subscription_credit":
        # A customer-level approved amount is meaningful only for its primary subscription.
        if not _same_id(params["subscription_id"], customer.get("subscription_id")):
            return None
        amount = _positive_amount(customer.get("subscription_credit_amount"))
        requested = context.get("credit_amount", context.get("amount"))
        quoted = re.findall(r"[$€£]\s*([0-9.,]+)", complaint)
        if (
            amount is None or (requested is not None and _positive_amount(requested) != amount)
            or any(_positive_amount(value.rstrip(".").replace(",", "")) != amount for value in quoted)
        ):
            return None
        params["amount"] = amount
    return params


def _default_driver() -> Any:
    """Default LLM driver: OpenAiDriver for the local LM Studio endpoint."""
    from app.providers.openai_driver import OpenAiDriver

    return OpenAiDriver(provider="lmstudio")


def _coerce_driver(driver: Any) -> Any:
    if driver is None:
        return _default_driver()
    if isinstance(driver, str):
        name = driver.strip().lower()
        if name in {"mock", "test", "offline"}:
            from app.providers.openai_driver import OpenAiDriver

            return OpenAiDriver(provider="lmstudio")
        if name == "openai":
            from app.providers.openai_driver import OpenAiDriver

            return OpenAiDriver(provider="openai", api_key=os.getenv("OPENAI_API_KEY") or "")
        from app.providers import create_driver

        return create_driver(name)
    return driver


# ---------------------------------------------------------------------------
# TroyClient
# ---------------------------------------------------------------------------


class TroyClient:
    """
    Universal AI resolution specialist client for any SaaS product.

    Args:
        config: :class:`SaaSConfig`, dict, JSON string, or path to a
            JSON/YAML config file. Defaults to ``SaaSConfig()``.
        adapter: data-store adapter. Defaults to ``InMemoryDataAdapter()``.
        tool_registry: tool registry. Defaults to a fresh registry
            pre-populated with standard refund/account tools.
        driver: LLM driver. Defaults to
            ``OpenAiDriver(provider='lmstudio')`` (local LM Studio).
    """

    def __init__(
        self,
        config: Optional[Union[SaaSConfig, Dict[str, Any], str, Path]] = None,
        adapter: Optional[DataStoreAdapter] = None,
        tool_registry: Optional[TroyToolRegistry] = None,
        driver: Any = None,
        review_queue: Optional[ReviewQueue] = None,
        **kwargs: Any,
    ) -> None:
        # Accept a few alias spellings without breaking the spec'd signature.
        if config is None:
            config = kwargs.pop("saas_config", None) or kwargs.pop("product_config", None)
        if adapter is None:
            adapter = kwargs.pop("store", None) or kwargs.pop("data_adapter", None) or kwargs.pop("data_store", None)
        if tool_registry is None:
            tool_registry = kwargs.pop("registry", None) or kwargs.pop("tools", None)
        if driver is None:
            driver = kwargs.pop("llm_driver", None) or kwargs.pop("llm", None)

        self.config: SaaSConfig = load_saas_config(config) if config is not None else SaaSConfig()
        self._adapter: DataStoreAdapter = adapter if adapter is not None else InMemoryDataAdapter()
        self.driver: Any = _coerce_driver(driver)
        queue_path = os.environ.get(REVIEW_DB_ENV, "").strip()
        self.review_queue = review_queue if review_queue is not None else (ReviewQueue(queue_path) if queue_path else None)
        self._tool_registry: TroyToolRegistry = tool_registry if tool_registry is not None else self._build_default_registry()
        self._lock = threading.RLock()

    # -- adapter / registry accessors (kept in sync) --------------------------

    @property
    def adapter(self) -> DataStoreAdapter:
        return self._adapter

    @adapter.setter
    def adapter(self, value: DataStoreAdapter) -> None:
        self._adapter = value

    @property
    def store(self) -> DataStoreAdapter:
        return self._adapter

    @store.setter
    def store(self, value: DataStoreAdapter) -> None:
        self._adapter = value

    @property
    def data_adapter(self) -> DataStoreAdapter:
        return self._adapter

    @property
    def tool_registry(self) -> TroyToolRegistry:
        return self._tool_registry

    @tool_registry.setter
    def tool_registry(self, value: TroyToolRegistry) -> None:
        self._tool_registry = value

    @property
    def registry(self) -> TroyToolRegistry:
        return self._tool_registry

    @registry.setter
    def registry(self, value: TroyToolRegistry) -> None:
        self._tool_registry = value

    @property
    def product_name(self) -> str:
        return self.config.product_name

    # -- default tools ---------------------------------------------------------

    def _build_default_registry(self) -> TroyToolRegistry:
        """Fresh registry pre-populated with standard refund/account tools."""
        registry = TroyToolRegistry()
        adapter_ref = self  # handlers read ``self.adapter`` at call time

        def _issue_refund(**params: Any) -> Dict[str, Any]:
            transaction_id = _first_str(params.get("transaction_id"), params.get("duplicate_txn_id"), params.get("txn_id"), params.get("id")) or ""
            amount = params.get("refund_amount", params.get("amount", 49.0))
            result = adapter_ref.adapter.execute_action(
                "remediate_duplicate_charge",
                {
                    "order_id": _first_str(params.get("order_id"), params.get("reference_order_id")) or "",
                    "duplicate_txn_id": transaction_id,
                    "transaction_id": transaction_id,
                    "customer_id": _first_str(params.get("customer_id")) or "",
                    "refund_amount": str(amount),
                    "amount": str(amount),
                    "processed_by": params.get("processed_by") or "Troy (Autonomous Resolution Agent)",
                    "notes": params.get("notes") or "",
                },
            )
            if isinstance(result, dict):
                result.setdefault("transaction_id", transaction_id)
                return result
            return {"ok": True, "status": "Unconfirmed", "result": result}

        def _get_customer(**params: Any) -> Dict[str, Any]:
            identifier = _first_str(params.get("identifier"), params.get("customer_id"), params.get("email"), params.get("order_id"), params.get("query")) or ""
            customer = adapter_ref.adapter.get_customer(identifier) if identifier else None
            return {"ok": customer is not None, "customer": customer, "account": customer}

        def _get_transactions(**params: Any) -> Dict[str, Any]:
            customer_id = _first_str(params.get("customer_id"), params.get("identifier")) or ""
            filters = {k: v for k, v in params.items() if k in {"order_id", "status", "billing_cycle", "transaction_id"} and v is not None}
            txns = adapter_ref.adapter.get_transactions(customer_id, **filters)
            return {"ok": True, "transactions": txns, "count": len(txns)}

        def _update_customer_account(**params: Any) -> Dict[str, Any]:
            customer_id = _first_str(params.get("customer_id"), params.get("id")) or ""
            updates = params.get("updates") if isinstance(params.get("updates"), Mapping) else {k: v for k, v in params.items() if k not in {"customer_id", "id", "updates"}}
            result = adapter_ref.adapter.execute_action(
                "update_customer_account",
                {"customer_id": customer_id, "updates": dict(updates)},
            )
            return result if isinstance(result, dict) else {"ok": True, "result": result}

        def _inspect(**params: Any) -> Dict[str, Any]:
            customer_id = _first_str(params.get("customer_id"), params.get("identifier")) or ""
            customer = adapter_ref.adapter.get_customer(customer_id) if customer_id else None
            effective_id = (customer or {}).get("customer_id") or customer_id
            txns = adapter_ref.adapter.get_transactions(effective_id) if effective_id else []
            settled = [t for t in txns if str(t.get("status", "")).lower() == "settled"]
            return {
                "ok": customer is not None or bool(txns),
                "customer": customer,
                "account": customer,
                "customer_id": effective_id,
                "transactions": txns,
                "has_duplicate": len(settled) > 1,
                "duplicate_detected": len(settled) > 1,
            }

        refund_schema = {
            "type": "object",
            "required": ["transaction_id"],
            "properties": {
                "transaction_id": {"type": "string"},
                "order_id": {"type": "string"},
                "customer_id": {"type": "string"},
                "refund_amount": {"type": "number"},
                "amount": {"type": "number"},
                "notes": {"type": "string"},
            },
        }

        registry.register_tool(_issue_refund, name="issue_refund", description="Issue a refund for a duplicate/erroneous charge.", schema=refund_schema)
        registry.register_tool(_issue_refund, name="refund", description="Alias for issue_refund.", schema=refund_schema)
        registry.register_tool(_issue_refund, name="remediate_duplicate_charge", description="Remediate a duplicate charge.", schema=refund_schema)
        registry.register_tool(
            _get_customer,
            name="get_customer",
            description="Look up a customer by ID, email, name, or order ID.",
            schema={"type": "object", "properties": {"customer_id": {"type": "string"}, "identifier": {"type": "string"}, "email": {"type": "string"}, "order_id": {"type": "string"}}},
        )
        registry.register_tool(
            _get_transactions,
            name="get_transactions",
            description="List transactions for a customer.",
            schema={"type": "object", "properties": {"customer_id": {"type": "string"}}},
        )
        registry.register_tool(
            _update_customer_account,
            name="update_customer_account",
            description="Update a customer account record.",
            schema={"type": "object", "required": ["customer_id"], "properties": {"customer_id": {"type": "string"}}},
        )
        registry.register_tool(
            _update_customer_account,
            name="update_account",
            description="Alias for update_customer_account.",
            schema={"type": "object", "required": ["customer_id"], "properties": {"customer_id": {"type": "string"}}},
        )
        registry.register_tool(
            _inspect,
            name="inspect_account_and_transactions",
            description="Inspect a customer account and its transactions, flagging duplicates.",
            schema={"type": "object", "properties": {"customer_id": {"type": "string"}}},
        )
        return registry

    # -- tool helpers ------------------------------------------------------------

    def register_tool(self, *args: Any, **kwargs: Any) -> Callable[..., Any]:
        """Register a custom tool on this client's registry (see TroyToolRegistry)."""
        return self.tool_registry.register_tool(*args, **kwargs)

    def tool(self, *args: Any, **kwargs: Any) -> Any:
        """Decorator form of :meth:`register_tool`."""
        return self.tool_registry.tool(*args, **kwargs)

    def execute_tool(self, tool_name: str, params: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        return self.tool_registry.execute(tool_name, params, **kwargs)

    def list_tools(self) -> List[Dict[str, Any]]:
        return self.tool_registry.list_tools()

    # -- introspection ------------------------------------------------------------

    def get_info(self) -> Dict[str, Any]:
        """Product and capability metadata (used by /health and /info routes)."""
        return _jsonable(
            {
                "ok": True,
                "product_name": self.config.product_name,
                "product_product_name": self.config.product_name,
                "product": {
                    "product_name": self.config.product_name,
                    "domain": self.config.domain,
                    "support_email": self.config.support_email,
                    "branding": self.config.branding,
                },
                "domain": self.config.domain,
                "support_email": self.config.support_email,
                "adapter_type": type(self.adapter).__name__,
                "adapter": type(self.adapter).__name__,
                "driver_type": type(self.driver).__name__ if self.driver is not None else None,
                "tools": self.tool_registry.list_tools(),
                "registered_tools": self.tool_registry.tool_names(),
                "categories": self.config.categories,
                "category_names": self.config.category_names(),
                "policies": self.config.policies,
                "entity_pattern_keys": list(self.config.entity_patterns.keys()),
            }
        )

    def get_health(self) -> Dict[str, Any]:
        info = self.get_info()
        return {
            **info,
            "status": "healthy",
            "healthy": True,
        }

    health = get_health
    info = get_info

    # -- intent evaluation ----------------------------------------------------------

    @staticmethod
    def _evaluate_intent(complaint: str, category: Optional[str] = None) -> Dict[str, Any]:
        try:
            from app.simulation.troy import Troy

            return Troy.evaluate_complaint_intent(complaint, category=category)
        except Exception:
            low = (complaint or "").lower()
            triggers = ("twice", "duplicate", "double charge", "charged twice", "refund", "overcharged", "double bill", "billed twice")
            is_billing = any(t in low for t in triggers) or category == "Refunds"
            return {
                "intent": "database_remediation" if is_billing else "direct_response",
                "is_billing_dispute": is_billing,
                "requires_db_mutation": is_billing,
                "category": category or ("Refunds" if is_billing else "General Inquiry"),
                "order_id": None,
                "customer_id": None,
                "transaction_id": None,
                "raw_text": complaint,
            }

    # -- LLM communication ------------------------------------------------------------

    async def _maybe_llm_communication(
        self,
        *,
        complaint: str,
        category: str,
        customer: Optional[Dict[str, Any]],
        action_result: Optional[Dict[str, Any]],
        fallback: str,
    ) -> tuple[str, Optional[str]]:
        driver = self.driver
        if driver is None or not hasattr(driver, "complete"):
            return fallback, None
        # Fast offline check for a local endpoint: if LM Studio is not
        # listening, skip the doomed network call and use the templated
        # message (the same outcome as a failed LLM call below).
        base_url = str(getattr(driver, "base_url", "") or "")
        if "localhost" in base_url or "127.0.0.1" in base_url:
            try:
                import socket
                from urllib.parse import urlparse

                parsed = urlparse(base_url if "://" in base_url else f"http://{base_url}")
                with socket.create_connection((parsed.hostname or "127.0.0.1", parsed.port or 1234), timeout=0.05):
                    pass
            except Exception:
                return fallback, None
        system = (
            f"You are Troy, the empathetic AI resolution specialist for "
            f"{self.config.product_name}. Write only the customer-facing "
            "resolution message: warm, specific, and free of internal jargon."
        )
        user = (
            f"Customer complaint: {complaint}\n"
            f"Category: {category}\n"
            f"Customer: {(customer or {}).get('customer_name') or 'valued customer'}\n"
            f"Action result: {action_result or 'none'}\n"
            "Write the resolution message to send to the customer."
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            try:
                raw = driver.complete(messages, 512)
            except TypeError:
                try:
                    raw = driver.complete(messages)
                except TypeError:
                    raw = driver.complete(user)
            if inspect.isawaitable(raw):
                raw = await raw
        except Exception as exc:
            logger.debug("LLM communication generation failed, using template: %s", exc)
            return fallback, None
        if isinstance(raw, Mapping):
            raw = raw.get("content") or raw.get("message") or raw.get("text") or ""
        text = re.sub(r"<thought>.*?</thought>", "", str(raw or ""), flags=re.IGNORECASE | re.DOTALL).strip()
        text = re.sub(r"^\s*###\s*\*\*?[^*\n]+", "", text).strip()
        if len(text) < 20 or "### **" in text:
            return fallback, (text or None)
        return text, text

    # -- core resolution ------------------------------------------------------------

    async def resolve_ticket(
        self,
        complaint: str,
        customer_id: Optional[str] = None,
        order_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        auto_execute: bool = True,
        dry_run: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Resolve a complaint while retaining claims with uncertain unsaved outcomes."""
        claim = {}
        result = None
        try:
            result = await self._resolve_ticket(
                complaint, customer_id, order_id, context, auto_execute, dry_run, claim=claim, **kwargs,
            )
            return result
        finally:
            saved_outcome = result is not None and result.get("review_queue_status") != "unavailable"
            if claim.get("token") and (not claim.get("attempted") or saved_outcome):
                try:
                    await asyncio.to_thread(self.review_queue.release_delivery, claim["key"], claim["token"])
                except (OSError, sqlite3.Error):
                    logger.warning("Could not release the local review delivery claim")

    async def _resolve_ticket(
        self,
        complaint: str,
        customer_id: Optional[str] = None,
        order_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        auto_execute: bool = True,
        dry_run: bool = False,
        *,
        claim: dict,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        # Alias arguments for convenience.
        if complaint is None:
            complaint = _first_str(kwargs.pop("query", None), kwargs.pop("text", None), kwargs.pop("message", None), kwargs.pop("ticket", None)) or ""
        context = dict(context or {})
        if "context" in kwargs and isinstance(kwargs["context"], Mapping):
            context.update(kwargs.pop("context"))
        customer_id = _first_str(customer_id, context.get("customer_id"), context.get("customerId"), kwargs.pop("customerId", None))
        order_id = _first_str(order_id, context.get("order_id"), context.get("orderId"), kwargs.pop("orderId", None))
        transaction_id = _first_str(context.get("transaction_id"), context.get("transactionId"), context.get("txn_id"), kwargs.pop("transaction_id", None), kwargs.pop("transactionId", None))
        category_hint = _first_str(context.get("category"), kwargs.pop("category", None))
        idempotency_key = _first_str(context.get("idempotency_key"), kwargs.pop("idempotency_key", None), kwargs.pop("idempotent_key", None))
        if "auto_execute" in kwargs:
            auto_execute = bool(kwargs.pop("auto_execute"))
        if "dry_run" in kwargs:
            dry_run = bool(kwargs.pop("dry_run"))

        complaint = (complaint or "").strip()
        if not complaint:
            return _jsonable({"ok": False, "status": "Failed", "error": "Complaint text is required.", "product_name": self.config.product_name})

        policies = self.config.policies
        effective_dry_run = bool(dry_run or getattr(policies, "dry_run", False))

        delivery_key = kwargs.pop("_verified_delivery_key", None)
        request_identifiers = [customer_id, order_id, transaction_id]

        # 1. Extract identifiers and reject conflicting account or resource selectors.
        entities = self.config.extract_entities(complaint)
        textual = {
            "customer": re.findall(r"\bUSR-\d+\b", complaint, re.I) + (entities.get("customers") or []),
            "order": re.findall(r"\bORD-\d+\b", complaint, re.I) + (entities.get("orders") or []),
            "transaction": re.findall(r"\bTXN-\d+\b", complaint, re.I) + (entities.get("transactions") or []),
        }
        selectors = (
            (customer_id, context.get("customer_id"), context.get("customerId"), *textual["customer"]),
            (order_id, context.get("order_id"), context.get("orderId"), *textual["order"]),
            (transaction_id, context.get("transaction_id"), context.get("transactionId"), context.get("txn_id"), *textual["transaction"]),
        )
        identifiers_conflict = any(len({str(value).strip().casefold() for value in values if value}) > 1 for values in selectors)
        customer_id, order_id, transaction_id = (_first_str(*values) for values in selectors)
        if transaction_id and re.fullmatch(r"TXN-\d+", transaction_id, re.I):
            transaction_id = transaction_id.upper()
        if order_id and re.fullmatch(r"ORD-\d+", order_id, re.I):
            order_id = order_id.upper()

        # 2. Evaluate intent.
        intent = self._evaluate_intent(complaint)
        if _INFORMATIONAL_QUESTION.match(complaint) or not _REFUND_REQUEST.search(complaint):
            intent.update(intent="direct_response", category=category_hint or "General Inquiry",
                          is_billing_dispute=False, requires_db_mutation=False)
        elif not intent.get("is_billing_dispute") and "policy" not in complaint.casefold():
            intent.update(intent="database_remediation", category="Refunds",
                          is_billing_dispute=True, requires_db_mutation=True)
        business_matches = _business_requests(complaint)
        business = business_matches[0] if business_matches else None
        mixed_request = len(business_matches) > 1 or bool(business and intent.get("is_billing_dispute"))
        if business and not intent.get("is_billing_dispute"):
            intent.update(intent="business_remediation", category=business[0],
                          is_billing_dispute=False, requires_db_mutation=True)
        elif mixed_request:
            business = None
        action_negated = mixed_request or bool(re.search(
            r"\b(?:do not|don['’]t|never|not|no|avoid|without)\s+(?:[\w-]+\s+){0,3}"
            r"(?:refund|unlock|reset|send|resend|email|generate|issue|apply|credit|retry|replay|redeliver|do it|take action)\b",
            complaint, re.I,
        ))
        category = str(intent.get("category") or category_hint or "General Inquiry")
        # Classification drives reporting and team routing. Action eligibility comes from intent and evidence.
        jev_model = configured_jev_model()
        classification = None
        if jev_model and not category_hint:
            criteria = {
                item.name: item.description or CATEGORY_DESCRIPTIONS.get(
                    SDK_CATEGORY_ALIASES.get(item.name, item.name), item.name,
                )
                for item in self.config.categories
            }
            classification = await classify_with_jev(complaint, jev_model, criteria)
            if classification:
                category = classification[0]["category"]
        category_config = self.config.get_category(category)
        if category_config is not None:
            category = category_config.name
        team = (category_config.team if category_config else None) or context.get("team") or "Tier 1 Support"
        sla = category_config.sla if category_config else {"estimated_hours": 8.0, "sla_risk": "Low"}

        # 3. Query only within the requested customer, order, or exact transaction.
        customer: Optional[Dict[str, Any]] = None
        transactions: List[Dict[str, Any]] = []
        effective_customer_id = customer_id
        if not identifiers_conflict and (customer_id or order_id or transaction_id):
            try:
                if customer_id:
                    record = self.adapter.get_customer(customer_id)
                    if record and _same_id(record.get("customer_id"), customer_id):
                        customer = record
                        effective_customer_id = _first_str(record["customer_id"])
                    transactions = list(self.adapter.get_transactions(effective_customer_id) or [])
                elif transaction_id:
                    matches = [
                        row for row in self.adapter.get_transactions("", transaction_id=transaction_id)
                        if isinstance(row, Mapping) and _same_id(row.get("transaction_id"), transaction_id)
                    ]
                    if len(matches) == 1:
                        owner = _first_str(matches[0].get("customer_id"))
                        order = _first_str(matches[0].get("order_id"), matches[0].get("reference_order_id"))
                        if owner and order and (not order_id or _same_id(order_id, order)):
                            effective_customer_id, order_id = owner, order
                            transactions = list(self.adapter.get_transactions(owner) or [])
                else:
                    transactions = [
                        row for row in self.adapter.get_transactions("", order_id=order_id)
                        if isinstance(row, Mapping) and _same_id(row.get("order_id"), order_id)
                    ]
                    owners = {str(row.get("customer_id") or "").strip().casefold() for row in transactions}
                    if len(owners) == 1 and "" not in owners:
                        effective_customer_id = _first_str(transactions[0].get("customer_id"))
                    else:
                        transactions = []
                if not customer and effective_customer_id and not customer_id:
                    record = self.adapter.get_customer(effective_customer_id)
                    if record and _same_id(record.get("customer_id"), effective_customer_id):
                        customer = record
            except Exception as exc:
                logger.debug("Scoped account or transaction lookup failed: %s", exc)
                transactions = []
        verified_customer = customer
        transactions = [dict(t) for t in transactions if isinstance(t, Mapping)]
        if effective_customer_id:
            transactions = [t for t in transactions if _same_id(t.get("customer_id"), effective_customer_id)]
        if order_id:
            transactions = [
                t for t in transactions if _same_id(t.get("order_id") or t.get("reference_order_id"), order_id)
            ]

        if order_id and not transactions and not _same_id(
            (verified_customer or {}).get("order_id") or (verified_customer or {}).get("reference_order_id"), order_id
        ):
            customer = verified_customer = None
        if transaction_id and not any(_same_id(t.get("transaction_id"), transaction_id) for t in transactions):
            customer = verified_customer = None

        # Keep same-order siblings for evidence, but require the requested transaction to match.
        candidate, duplicate_detected, is_already_refunded = (None, False, False)
        if not business and not action_negated and intent.get("requires_db_mutation") and (effective_customer_id or order_id or transaction_id):
            candidate, duplicate_detected, is_already_refunded = _refund_evidence(transactions, transaction_id)
        if candidate:
            transaction_id = _first_str(candidate.get("transaction_id"))
            order_id = _first_str(candidate.get("order_id"), candidate.get("reference_order_id"))
            effective_customer_id = _first_str(candidate.get("customer_id"))
        if transaction_id:
            transactions = [
                t for t in transactions
                if _same_id(t.get("transaction_id"), transaction_id)
                or (candidate and _same_id(t.get("order_id") or t.get("reference_order_id"), order_id)
                    and _same_id(t.get("customer_id"), effective_customer_id))
            ]

        amount_value = _positive_amount((candidate or {}).get("amount"))
        remediation_needed = duplicate_detected
        refund_needs_review = bool(not business and intent.get("requires_db_mutation") and not remediation_needed and not is_already_refunded)
        approval_flag = bool(policies.approval_required or policies.require_approval)
        refund_limits = [policies.auto_refund_limit]
        if policies.approval_threshold is not None:
            refund_limits.append(policies.approval_threshold)
        requires_approval = bool(remediation_needed and (
            approval_flag or any(not math.isfinite(limit) or amount_value > limit for limit in refund_limits)
        ))

        prior_case = None
        replay_key = None
        queue_unavailable = False
        if self.review_queue is not None and not effective_dry_run and (delivery_key or idempotency_key):
            if delivery_key:
                account = _first_str(customer_id, effective_customer_id)
                if identifiers_conflict:
                    scope = ["conflict", selectors]
                else:
                    scope = ["customer", account.casefold()] if account else ["resource", order_id, transaction_id, complaint]
                identity = ["delivery", delivery_key, scope]
            else:
                identity = [
                    "request", idempotency_key, complaint, *request_identifiers, category_hint,
                    {key: context.get(key) for key in ("invoice_id", "subscription_id", "event_id", "credit_amount", "amount")},
                ]
            replay_key = hashlib.sha256(json.dumps(_jsonable(identity), sort_keys=True).encode()).hexdigest()
            try:
                prior_case = await asyncio.to_thread(self.review_queue.find, replay_key)
                if delivery_key and not prior_case:
                    legacy_key = hashlib.sha256(json.dumps(["delivery", delivery_key]).encode()).hexdigest()
                    legacy = await asyncio.to_thread(self.review_queue.find, legacy_key)
                    if legacy and account and not identifiers_conflict and _same_id(legacy["customer_id"], account):
                        prior_case = legacy
                if delivery_key and not prior_case:
                    acquisition = asyncio.create_task(asyncio.to_thread(
                        self.review_queue.claim_delivery, replay_key, delivery_key=delivery_key, legacy_key=legacy_key,
                        category=category, team=team,
                        possible_action=business[1] if business else ("issue_refund" if remediation_needed else None),
                        customer_id=effective_customer_id,
                        order_id=order_id, transaction_id=transaction_id,
                    ))
                    try:
                        token = await asyncio.shield(acquisition)
                    except asyncio.CancelledError:
                        token = await acquisition
                        if token:
                            claim.update(key=replay_key, token=token)
                        raise
                    if not token:
                        return {
                            "ok": False, "status": "delivery_in_progress",
                            "summary": "This webhook delivery is already being processed.",
                            "customer_communication": "The original request is still being processed.",
                        }
                    claim.update(key=replay_key, token=token)
                    prior_case = await asyncio.to_thread(self.review_queue.find, replay_key)
                if delivery_key and idempotency_key == delivery_key:
                    idempotency_key = "webhook-review:" + replay_key
            except UnscopedDeliveryHold:
                return {
                    "ok": False, "status": "delivery_requires_reconciliation",
                    "summary": "An earlier delivery requires reconciliation before it can be matched to a customer.",
                    "customer_communication": "This delivery needs operator review.",
                }
            except (OSError, sqlite3.Error):
                queue_unavailable = True
                logger.warning("Could not check the local review queue")

        # 5. Execute remediation if needed (respecting dry-run + policies).
        action_result: Optional[Dict[str, Any]] = None
        action_name: Optional[str] = None
        business_reason = None
        if business:
            _, tool_name, description, _, _ = business
        if prior_case or queue_unavailable:
            action_name = prior_case["action"] if prior_case else None
        elif business:
            tool_params = _business_parameters(business, verified_customer, complaint, context, entities)
            if action_negated or identifiers_conflict:
                business_reason = "The request contains conflicting instructions or identifiers."
            elif tool_name == "send_invoice" and re.search(r"\b(generate|issue)\b", complaint, re.I) and not re.search(
                r"\b(send|resend|email|deliver)\b", complaint, re.I
            ):
                business_reason = "The configured invoice action supports delivery of an existing invoice."
            elif tool_name == "retry_webhook" and not re.search(r"\b(retry|replay|redeliver|resend)\b", complaint, re.I):
                business_reason = "A webhook retry was not explicitly requested."
            elif tool_name == "apply_subscription_credit" and not re.search(r"\bcredit\b", complaint, re.I):
                business_reason = "A subscription credit was not explicitly requested."
            elif not self.tool_registry.has_tool(tool_name):
                business_reason = "No matching business tool is configured."
            elif not tool_params:
                business_reason = "The required account or resource details could not be verified."
            else:
                action_name = tool_name
                remediation_needed = True
                credit_limit = policies.auto_credit_limit
                threshold = policies.approval_threshold
                limits = [limit for limit in (credit_limit, threshold) if limit is not None]
                credit = tool_params.get("amount")
                requires_approval = approval_flag or (
                    credit is not None and any(not math.isfinite(limit) or credit > limit for limit in limits)
                )
                if effective_dry_run:
                    action_result = {"ok": True, "status": "dry_run", "action": action_name, "params": tool_params}
                elif not auto_execute:
                    action_result = {"ok": True, "status": "not_executed", "action": action_name, "params": tool_params}
                elif requires_approval:
                    action_result = {"ok": False, "status": "pending_approval", "action": action_name, "params": tool_params}
                else:
                    claim["attempted"] = True
                    action_result = self.tool_registry.execute(
                        action_name, tool_params, idempotency_key=idempotency_key,
                    )
        elif remediation_needed:
            action_name = "issue_refund"
            tool_params: Dict[str, Any] = {
                "transaction_id": transaction_id or "",
                "order_id": order_id or "",
                "customer_id": effective_customer_id or "",
                "refund_amount": amount_value,
                "amount": amount_value,
                "processed_by": "Troy (Autonomous Resolution Agent)",
                "notes": f"Autonomous refund executed by Troy for order {order_id or 'unknown'}.",
            }
            idem = idempotency_key or (f"{effective_customer_id}:{transaction_id}:{order_id}" if transaction_id else None)
            if not auto_execute:
                action_result = {
                    "ok": True,
                    "status": "not_executed",
                    "action": action_name,
                    "recommended_action": action_name,
                    "reason": "auto_execute is disabled",
                    "params": tool_params,
                }
            elif requires_approval:
                action_result = {
                    "ok": False,
                    "status": "pending_approval",
                    "action": action_name,
                    "recommended_action": action_name,
                    "requires_approval": True,
                    "reason": f"The ledger amount requires approval under {self.config.product_name} policy.",
                    "params": tool_params,
                }
            elif self.tool_registry.has_tool(action_name):
                claim["attempted"] = True
                action_result = self.tool_registry.execute(
                    action_name,
                    tool_params,
                    idempotency_key=idem,
                    dry_run=effective_dry_run,
                )
            elif self.tool_registry.has_tool("remediate_duplicate_charge"):
                claim["attempted"] = True
                action_result = self.tool_registry.execute(
                    "remediate_duplicate_charge",
                    tool_params,
                    idempotency_key=idem,
                    dry_run=effective_dry_run,
                )
            elif effective_dry_run:
                action_result = {
                    "ok": True,
                    "status": "dry_run",
                    "action": action_name,
                    "dry_run": True,
                    "params": tool_params,
                }
            else:
                try:
                    claim["attempted"] = True
                    raw = self.adapter.execute_action(
                        "remediate_duplicate_charge",
                        {
                            "order_id": order_id or "",
                            "duplicate_txn_id": transaction_id or "",
                            "transaction_id": transaction_id or "",
                            "customer_id": effective_customer_id or "",
                            "refund_amount": str(amount_value),
                            "amount": str(amount_value),
                        },
                    )
                    action_result = dict(raw) if isinstance(raw, Mapping) else {"ok": True, "status": "Unconfirmed", "result": raw}
                    action_result.setdefault("action", action_name)
                    action_result.setdefault("status", "Completed" if action_result.get("ok") else "Failed")
                except Exception as exc:
                    action_result = {"ok": False, "status": "Failed", "action": action_name, "error": str(exc)}
        elif is_already_refunded:
            action_result = {
                "ok": True,
                "status": "already_refunded",
                "action": "issue_refund",
                "transaction_id": transaction_id,
                "refund_id": (candidate or {}).get("refund_id"),
                "refund_amount": str(amount_value),
            }

        if action_result and action_result.get("in_progress"):
            return {
                "ok": False, "status": "action_in_progress",
                "summary": "An action with this request key is already being processed.",
                "customer_communication": "The original request is still being processed.",
            }

        action_status = str((action_result or {}).get("status") or "") if action_result else "none"
        action_ok = bool(action_result and action_result.get("ok"))
        outcome = action_status.strip().lower()
        completion_statuses = {"completed", "updated"}
        if business:
            completion_statuses.add({
                "send_invoice": "sent", "unlock_account": "unlocked",
                "apply_subscription_credit": "credited", "retry_webhook": "retried",
            }[business[1]])
        else:
            completion_statuses.add("refunded")
        action_confirmed = action_ok and outcome in completion_statuses
        refund_id = None
        if not business and (action_confirmed or is_already_refunded):
            refund_id = _first_str((action_result or {}).get("refund_id"), (candidate or {}).get("refund_id"))

        if prior_case:
            status = prior_case["reason"]
            requires_approval = status == "pending_approval"
            category, team = prior_case["category"], prior_case["team"]
        elif queue_unavailable:
            status = "needs_review"
        elif effective_dry_run and remediation_needed:
            status = "dry_run"
        elif requires_approval and remediation_needed and auto_execute:
            status = "pending_approval"
        elif is_already_refunded:
            status = "already_refunded"
        elif refund_needs_review or business_reason:
            status = "needs_review"
        elif remediation_needed and not auto_execute:
            status = "not_executed"
        elif remediation_needed and not action_confirmed:
            status = "action_unconfirmed" if action_ok and outcome not in {"failed", "error"} else "action_failed"
        else:
            status = "resolved"
        ok = status not in {"action_failed", "action_unconfirmed"}

        # 6. Customer communication + structured report.
        customer_name = _first_str((customer or {}).get("customer_name"), (customer or {}).get("name")) or "valued customer"
        first_name = customer_name.split()[0] if customer_name else "valued customer"
        product = self.config.product_name
        support_email = self.config.support_email
        currency = _first_str((candidate or {}).get("currency"))
        amount_str = "the recorded amount"
        if amount_value is not None:
            amount_str = (
                f"{amount_value:.2f} {currency.upper()}"
                if currency and currency.upper() != "USD" else f"${amount_value:.2f}"
            )

        if business:
            business_message, next_step = {
                "resolved": (f"The configured tool confirmed completion of your {description} request.", "Record the tool outcome in the ticket"),
                "needs_review": (f"Your {description} request needs manual review.", "Verify the required records and route to the recommended team"),
                "dry_run": (f"This run previewed your {description} request. No action was executed.", "Review the preview and policy requirements before execution"),
                "pending_approval": (f"Your {description} request requires approval. No action was executed.", "Obtain approval before executing the action"),
                "not_executed": (f"Automatic execution is disabled. Your {description} request is still open.", "Review the recommendation before executing the action"),
                "action_failed": (f"The configured tool could not complete your {description} request.", "Investigate the tool failure before retrying"),
                "action_unconfirmed": (f"The configured tool did not confirm completion of your {description} request.", "Verify the tool outcome before retrying"),
            }[status]
            comm_body = business_message
        elif status == "dry_run":
            comm_body = (
                f"we would refund the duplicate charge of {amount_str}"
                + (f" for transaction {transaction_id}" if transaction_id else "")
                + ", but this run is in dry-run mode, so no change has been made yet."
            )
        elif status == "pending_approval":
            comm_body = (
                f"your refund request for {amount_str} requires approval under our policy. "
                "No refund was issued."
            )
        elif status == "already_refunded":
            comm_body = (
                f"the duplicate charge of {amount_str}"
                + (f" for transaction {transaction_id}" if transaction_id else "")
                + " is already marked refunded in the records"
                + (f" under reference {refund_id}" if refund_id else "")
                + "."
            )
        elif status == "needs_review":
            comm_body = (
                "Your instructions or identifiers conflict. No refund was attempted."
                if action_negated or identifiers_conflict else
                "We could not verify the duplicate charge from the available records. Your request needs manual review."
            )
        elif action_confirmed:
            comm_body = (
                f"we confirmed an accidental duplicate charge of {amount_str}"
                + (f" for order {order_id}" if order_id else "")
                + (f" (transaction {transaction_id})" if transaction_id else "")
                + " and the configured refund action reported completion"
                + (f" under reference {refund_id}" if refund_id else "")
                + "."
            )
        elif not ok:
            comm_body = (
                "We could not confirm an automatic refund for your duplicate charge. "
                "Your case needs manual review."
            )
        elif status == "not_executed":
            comm_body = (
                f"We identified a potential refund of {amount_str} for order "
                f"{order_id or 'your recent order'}. The refund has not been issued."
            )
        else:
            comm_body = (
                "we have reviewed your request and our team will follow up shortly within the SLA. "
                "Thank you for bringing this to our attention."
            )

        use_template = bool(business) or status in {"not_executed", "needs_review"} or not ok
        template = (
            f"Hello {first_name},\n\n"
            f"Thank you for contacting {product} support — we are sorry for the trouble. "
            f"Regarding your complaint: \"{complaint}\"\n\n"
            f"{comm_body if use_template else 'Good news: ' + comm_body}\n\n"
            f"If you have any questions, simply reply to this message or contact us at {support_email}.\n\n"
            f"Warm regards,\nTroy — AI Resolution Specialist, {product}"
        )
        communication, llm_response = template, None
        if not use_template and not (remediation_needed or is_already_refunded):
            communication, llm_response = await self._maybe_llm_communication(
                complaint=complaint,
                category=category,
                customer=customer,
                action_result=action_result,
                fallback=template,
            )

        summary = (
            f"Complaint classified as '{category}' for {product}; "
            + ("refund action reported completion." if status == "resolved" and remediation_needed else
               "duplicate charge already refunded." if status == "already_refunded" else
               "dry-run only; no records changed." if status == "dry_run" else
               "refund pending approval." if status == "pending_approval" else
               "refund not executed because automatic execution is disabled." if status == "not_executed" else
               "automatic refund failed." if status == "action_failed" else
               "automatic refund could not be confirmed." if status == "action_unconfirmed" else
               ("duplicate refund requires manual review because instructions or identifiers conflict."
                if action_negated or identifiers_conflict else
                "duplicate refund requires manual review because the ledger evidence is incomplete.") if status == "needs_review" else
               "no database remediation required.")
        )
        next_steps = (
            (["Clarify the requested action and identifiers"] if action_negated or identifiers_conflict
             else ["Verify the ledger before approving a refund"]) if status == "needs_review" else
            ["Review and execute the recommended refund"] if status == "not_executed" else
            ["Review the refund attempt and verify the transaction before retrying"] if not ok else
            [
                "Share refund confirmation with the customer" if action_confirmed else "Customer follow-up within SLA",
                "Record the verified outcome in the ticket" if status in {"resolved", "already_refunded"} else "Awaiting approval / dry-run confirmation",
            ]
        )
        if business:
            summary = f"Complaint classified as '{category}' for {product}. {business_message}"
            if business_reason:
                summary += f" {business_reason}"
            next_steps = [next_step]
        review_case = prior_case
        if queue_unavailable:
            summary = "Review queue lookup failed. No action was attempted."
            communication = summary
            next_steps = []
        if (
            self.review_queue is not None and not effective_dry_run and not prior_case and not queue_unavailable
            and status in {"needs_review", "pending_approval", "not_executed", "action_failed", "action_unconfirmed"}
        ):
            try:
                save = asyncio.create_task(asyncio.to_thread(
                    self.review_queue.enqueue, replay_key,
                    reason=status, category=category, team=team, summary=summary, action=action_name,
                    customer_id=effective_customer_id, order_id=order_id, transaction_id=transaction_id,
                    sla_hours=sla.get("estimated_hours"),
                ))
                try:
                    review_case, inserted = await asyncio.shield(save)
                except asyncio.CancelledError:
                    await save
                    raise
                if not inserted:
                    prior_case = review_case
            except (OSError, sqlite3.Error):
                queue_unavailable = True
                logger.warning("Could not save a case to the local review queue")

        review_metadata = {}
        if review_case:
            case_id, case_status = review_case["case_id"], review_case["status"]
            review_metadata = {"review_case_id": case_id, "review_queue_status": case_status, "review_case_replay": bool(prior_case)}
            if prior_case:
                summary = review_case["summary"]
                next_steps = []
            queue_message = (
                f"Your request is in the review queue under case {case_id}." if case_status == "open" else
                f"Review case {case_id} is marked {case_status}."
            )
            if case_status == "open":
                next_steps = [f"Review case {case_id} in the local queue", *next_steps]
            summary += " " + queue_message
            communication = queue_message if prior_case else communication + "\n\n" + queue_message
        elif queue_unavailable:
            review_metadata = {"review_queue_status": "unavailable"}
            queue_message = "The request could not be recorded in the review queue."
            summary += " " + queue_message
            communication += "\n\n" + queue_message
            next_steps = ["Record the request for manual follow-up once the queue is available", *next_steps]

        action_taken = action_result if status not in {"not_executed", "pending_approval", "dry_run"} else None
        report = {
            **review_metadata,
            "title": f"{category} resolution — {order_id or effective_customer_id or 'customer ticket'}",
            "product_name": product,
            "category": category,
            "team": team,
            "recommended_team": team,
            "sla": sla,
            "summary": summary,
            "findings": {
                "entities": entities,
                "customer_found": customer is not None,
                "transaction_count": len(transactions),
                "duplicate_detected": duplicate_detected,
                "is_already_refunded": is_already_refunded,
                "candidate_transaction_id": transaction_id,
            },
            "actions_taken": [action_taken] if action_taken else [],
            "action_result": action_result,
            "next_steps": next_steps,
            "customer_communication": communication,
        }

        payload: Dict[str, Any] = {
            **review_metadata,
            "ok": ok,
            "status": status,
            "product_name": product,
            "product": product,
            "complaint": complaint,
            "query": complaint,
            "customer_id": effective_customer_id,
            "order_id": order_id,
            "transaction_id": transaction_id,
            "entities": entities,
            "extracted_entities": entities,
            "intent": intent,
            "category": category,
            "predicted_category": category,
            "team": team,
            "recommended_team": team,
            "sla": sla,
            "customer": customer,
            "account": customer,
            "transactions": transactions,
            "duplicate_detected": duplicate_detected,
            "has_duplicate": bool(duplicate_detected or is_already_refunded),
            "is_already_refunded": is_already_refunded,
            "already_refunded": is_already_refunded,
            "remediation_needed": remediation_needed,
            "requires_approval": requires_approval,
            "dry_run": effective_dry_run,
            "auto_execute": auto_execute,
            "action": action_name,
            "action_status": action_status,
            "action_result": action_result,
            "execution": action_result,
            "remediation": action_result,
            "action_taken": action_taken,
            "refund_id": refund_id,
            "refund_amount": f"{amount_value:.2f}" if amount_value is not None else None,
            "customer_communication": communication,
            "communication": communication,
            "message": communication,
            "response": communication,
            "summary": summary,
            "resolution": report,
            "resolution_report": report,
            "report": report,
            "context": context,
            "timestamp": _utcnow_iso(),
        }
        if classification:
            payload.update(
                classification_method="jev",
                category_confidence=classification[0]["score"],
                top_3_categories=classification,
            )
            if prior_case:
                payload["predicted_category"] = classification[0]["category"]
        elif jev_model and not category_hint:
            payload["classification_method"] = "intent_fallback"
        if llm_response:
            payload["llm_response"] = llm_response

        # 7. Audit trail.
        try:
            self.adapter.audit_log(
                {
                    "event": "resolve_ticket",
                    **review_metadata,
                    "product_name": product,
                    "complaint": complaint,
                    "category": category,
                    "customer_id": effective_customer_id,
                    "order_id": order_id,
                    "transaction_id": transaction_id,
                    "status": status,
                    "dry_run": effective_dry_run,
                    "auto_execute": auto_execute,
                    "action": action_name,
                    "action_status": action_status,
                    "summary": summary,
                    "next_steps": next_steps,
                    "ok": ok,
                }
            )
        except Exception as exc:
            logger.debug("adapter audit_log failed: %s", exc)

        return _jsonable(payload)

    def resolve_ticket_sync(
        self,
        complaint: str,
        customer_id: Optional[str] = None,
        order_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        auto_execute: bool = True,
        dry_run: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Synchronous wrapper around :meth:`resolve_ticket`."""
        coro = self.resolve_ticket(
            complaint,
            customer_id=customer_id,
            order_id=order_id,
            context=context,
            auto_execute=auto_execute,
            dry_run=dry_run,
            **kwargs,
        )
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        # Already inside a running loop: run in a helper thread.
        outcome: Dict[str, Any] = {}

        def _runner() -> None:
            outcome["result"] = asyncio.run(coro)

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
        thread.join()
        return outcome.get("result", {"ok": False, "status": "Failed", "error": "resolve_ticket did not complete"})

    # Aliases
    resolve = resolve_ticket
    resolve_sync = resolve_ticket_sync


__all__ = ["TroyClient"]
