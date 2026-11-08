"""
TroyClient — the plug-and-play SDK entry point for Troy.

A SaaS operator creates one client, optionally supplying their
:class:`~app.core.saas_config.SaaSConfig`, a data-store adapter, a tool
registry, and an LLM driver::

    from app.sdk import TroyClient
    from app.core.adapter import InMemoryDataAdapter

    client = TroyClient(
        config={"product_name": "CloudScribe", "domain": "cloudscribe.io"},
        adapter=InMemoryDataAdapter(customers=[...], transactions=[...]),
    )
    result = await client.resolve_ticket(
        "I was charged twice for order ORD-88219 (USR-10492)",
    )

With no arguments the client is fully zero-setup: a default
``SaaSConfig``, an empty :class:`InMemoryDataAdapter`, a tool registry
pre-populated with standard refund/account tools, and a ``MockDriver``
for offline/test use (an OpenAI driver is used instead when an
``OPENAI_API_KEY`` is present in the environment).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Union

from app.core.adapter import DataStoreAdapter, InMemoryDataAdapter
from app.core.saas_config import SaaSConfig, load_saas_config
from app.core.tool_registry import TroyToolRegistry

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _parse_amount(value: Any, default: float = 49.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    match = re.search(r"([\d,]+\.?\d*)", str(value))
    if not match:
        return default
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return default


def _default_driver() -> Any:
    """MockDriver for offline/test use, or OpenAI driver if available."""
    api_key = os.getenv("OPENAI_API_KEY") or ""
    provider_hint = (os.getenv("TROY_LLM_PROVIDER") or os.getenv("LLM_PROVIDER") or "").strip().lower()
    if api_key and provider_hint in {"", "openai"}:
        try:
            from app.providers.openai_driver import OpenAiDriver

            return OpenAiDriver(
                provider="openai",
                model=os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL") or "gpt-4o-mini",
                api_key=api_key,
                base_url=os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1",
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("OpenAI driver unavailable, falling back to MockDriver: %s", exc)
    from app.providers.mock_driver import MockDriver

    return MockDriver()


def _coerce_driver(driver: Any) -> Any:
    if driver is None:
        return _default_driver()
    if isinstance(driver, str):
        name = driver.strip().lower()
        if name in {"mock", "test", "offline"}:
            from app.providers.mock_driver import MockDriver

            return MockDriver()
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
        driver: LLM driver. Defaults to ``MockDriver()`` for
            zero-dependency offline/test mode, or an OpenAI driver when
            ``OPENAI_API_KEY`` is available.
    """

    def __init__(
        self,
        config: Optional[Union[SaaSConfig, Dict[str, Any], str, Path]] = None,
        adapter: Optional[DataStoreAdapter] = None,
        tool_registry: Optional[TroyToolRegistry] = None,
        driver: Any = None,
        **kwargs: Any,
    ) -> None:
        # Accept a few alias spellings without breaking the spec'd signature.
        if config is None:
            config = kwargs.pop("saas_config", None) or kwargs.pop("tenant_config", None)
        if adapter is None:
            adapter = kwargs.pop("store", None) or kwargs.pop("data_adapter", None) or kwargs.pop("data_store", None)
        if tool_registry is None:
            tool_registry = kwargs.pop("registry", None) or kwargs.pop("tools", None)
        if driver is None:
            driver = kwargs.pop("llm_driver", None) or kwargs.pop("llm", None)

        self.config: SaaSConfig = load_saas_config(config) if config is not None else SaaSConfig()
        self._adapter: DataStoreAdapter = adapter if adapter is not None else InMemoryDataAdapter()
        self.driver: Any = _coerce_driver(driver)
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
            return {"ok": True, "result": result}

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
        """Tenant and capability metadata (used by /health and /info routes)."""
        return _jsonable(
            {
                "ok": True,
                "product_name": self.config.product_name,
                "tenant_product_name": self.config.product_name,
                "tenant": {
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
        # The deterministic MockDriver is used for offline/test mode; the
        # templated communication is cleaner and fully deterministic there.
        if type(driver).__name__ == "MockDriver" or str(getattr(driver, "provider", "")).lower() == "mock":
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
        """
        Resolve a customer complaint end-to-end and return a clean,
        JSON-serialisable resolution payload.
        """
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

        # 1. Extract entities.
        try:
            entities = self.config.extract_entities(complaint)
        except Exception:
            entities = {}
        customer_id = customer_id or (entities.get("customers") or [None])[0]
        order_id = order_id or (entities.get("orders") or [None])[0]
        transaction_id = transaction_id or (entities.get("transactions") or [None])[0]

        # 2. Evaluate intent.
        intent = self._evaluate_intent(complaint, category=category_hint)
        customer_id = customer_id or intent.get("customer_id")
        order_id = order_id or intent.get("order_id")
        transaction_id = transaction_id or intent.get("transaction_id")
        category = str(intent.get("category") or category_hint or "General Inquiry")
        category_config = self.config.get_category(category)
        if category_config is not None:
            category = category_config.name
        team = (category_config.team if category_config else None) or context.get("team") or "Tier 1 Support"
        sla = category_config.sla if category_config else {"estimated_hours": 8.0, "sla_risk": "Low"}

        # 3. Query customer & transactions via the adapter.
        customer: Optional[Dict[str, Any]] = None
        for candidate in dict.fromkeys([x for x in [customer_id, order_id, transaction_id, complaint] if x]):
            try:
                customer = self.adapter.get_customer(str(candidate))
            except Exception as exc:
                logger.debug("get_customer(%s) failed: %s", candidate, exc)
                customer = None
            if customer:
                break
        effective_customer_id = _first_str((customer or {}).get("customer_id"), customer_id, transaction_id and None) or customer_id

        transactions: List[Dict[str, Any]] = []
        try:
            if effective_customer_id:
                transactions = list(self.adapter.get_transactions(effective_customer_id) or [])
            elif order_id:
                try:
                    transactions = list(self.adapter.get_transactions("", order_id=order_id) or [])
                except TypeError:
                    transactions = list(self.adapter.get_transactions(order_id) or [])
            else:
                transactions = list(self.adapter.get_transactions("") or [])
        except Exception as exc:
            logger.debug("get_transactions failed: %s", exc)
            transactions = []
        transactions = [dict(t) for t in transactions if isinstance(t, Mapping)]

        if order_id:
            by_order = [t for t in transactions if str(t.get("order_id") or t.get("reference_order_id") or "").upper() == str(order_id).upper()]
            if by_order:
                transactions = by_order
        if transaction_id:
            by_txn = [t for t in transactions if str(t.get("transaction_id") or "").upper() == str(transaction_id).upper()]
            if by_txn:
                transactions = by_txn
        if not effective_customer_id and transactions:
            effective_customer_id = _first_str(transactions[0].get("customer_id")) or effective_customer_id
        if not customer and transactions:
            customer = {
                "customer_id": transactions[0].get("customer_id", effective_customer_id or ""),
                "customer_name": transactions[0].get("customer_name", ""),
                "customer_email": transactions[0].get("customer_email", ""),
                "reference_order_id": transactions[0].get("order_id", order_id or ""),
            }

        # 4. Duplicate / refund analysis.
        settled = [t for t in transactions if str(t.get("status", "")).lower() == "settled"]
        refunded = [t for t in transactions if str(t.get("status", "")).lower() == "refunded"]
        duplicate_detected = len(settled) > 1
        is_already_refunded = bool(refunded) and len(settled) <= 1

        candidate: Optional[Dict[str, Any]] = None
        if duplicate_detected:
            ordered = sorted(settled, key=lambda t: str(t.get("transaction_date", "")))
            candidate = ordered[1] if len(ordered) > 1 else ordered[0]
        elif is_already_refunded and refunded:
            candidate = refunded[0]
        elif transaction_id:
            match = next((t for t in transactions if str(t.get("transaction_id", "")).upper() == str(transaction_id).upper()), None)
            candidate = match or {
                "transaction_id": transaction_id,
                "customer_id": effective_customer_id or "",
                "order_id": order_id or "",
                "amount": str(context.get("amount") or context.get("refund_amount") or "49.00"),
                "status": "Settled",
            }
        elif bool(intent.get("requires_db_mutation")) and transactions:
            non_refunded = [t for t in transactions if str(t.get("status", "")).lower() != "refunded"]
            candidate = non_refunded[-1] if non_refunded else transactions[-1]

        if candidate is not None:
            transaction_id = _first_str(candidate.get("transaction_id"), transaction_id) or transaction_id
            order_id = _first_str(candidate.get("order_id"), candidate.get("reference_order_id"), order_id) or order_id
            effective_customer_id = _first_str(candidate.get("customer_id"), effective_customer_id) or effective_customer_id
            if str(candidate.get("status", "")).lower() == "refunded":
                is_already_refunded = True

        amount_value = _parse_amount(
            context.get("refund_amount") or context.get("amount") or (candidate or {}).get("refund_amount") or (candidate or {}).get("amount"),
            default=49.0,
        )
        remediation_needed = bool(
            intent.get("requires_db_mutation")
            and candidate is not None
            and not is_already_refunded
            and (duplicate_detected or bool(intent.get("is_billing_dispute")) or category.lower() in {"refunds", "billing"})
        )
        approval_flag = bool(getattr(policies, "approval_required", False) or getattr(policies, "require_approval", False))
        try:
            over_limit = amount_value > float(getattr(policies, "auto_refund_limit", 50.0))
        except (TypeError, ValueError):
            over_limit = False
        requires_approval = bool(remediation_needed and (approval_flag or over_limit))

        # 5. Execute remediation if needed (respecting dry-run + policies).
        action_result: Optional[Dict[str, Any]] = None
        action_name: Optional[str] = None
        if remediation_needed:
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
                    "reason": f"Amount ${amount_value:.2f} requires approval under {self.config.product_name} policy.",
                    "params": tool_params,
                }
            elif self.tool_registry.has_tool(action_name):
                action_result = self.tool_registry.execute(
                    action_name,
                    tool_params,
                    idempotency_key=idem,
                    dry_run=effective_dry_run,
                )
            elif self.tool_registry.has_tool("remediate_duplicate_charge"):
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
                    action_result = dict(raw) if isinstance(raw, Mapping) else {"ok": True, "result": raw}
                    action_result.setdefault("action", action_name)
                except Exception as exc:
                    action_result = {"ok": False, "status": "Failed", "action": action_name, "error": str(exc)}
        elif is_already_refunded:
            action_result = {
                "ok": True,
                "status": "already_refunded",
                "action": "issue_refund",
                "transaction_id": transaction_id,
                "refund_id": (candidate or {}).get("refund_id") or (refunded[0].get("refund_id") if refunded else ""),
                "refund_amount": str(amount_value),
            }

        refund_id = _first_str((action_result or {}).get("refund_id"), (candidate or {}).get("refund_id")) if action_result else None
        if not refund_id and is_already_refunded and refunded:
            refund_id = _first_str(refunded[0].get("refund_id"))
        action_status = str((action_result or {}).get("status") or "") if action_result else "none"

        if effective_dry_run and remediation_needed:
            status = "dry_run"
        elif requires_approval and remediation_needed and auto_execute:
            status = "pending_approval"
        elif is_already_refunded:
            status = "already_refunded"
        else:
            status = "resolved"

        # 6. Customer communication + structured report.
        customer_name = _first_str((customer or {}).get("customer_name"), (customer or {}).get("name")) or "valued customer"
        first_name = customer_name.split()[0] if customer_name else "valued customer"
        product = self.config.product_name
        support_email = self.config.support_email
        amount_str = f"${amount_value:.2f}"

        if status == "dry_run":
            comm_body = (
                f"we would refund the duplicate charge of {amount_str}"
                + (f" for transaction {transaction_id}" if transaction_id else "")
                + ", but this run is in dry-run mode, so no change has been made yet."
            )
        elif status == "pending_approval":
            comm_body = (
                f"your case for {amount_str} has been flagged for approval under our policy "
                "and will be reviewed by our team within the SLA. No charge change has been made yet."
            )
        elif status == "already_refunded":
            comm_body = (
                f"the duplicate charge of {amount_str}"
                + (f" for transaction {transaction_id}" if transaction_id else "")
                + f" was already refunded under reference {refund_id or 'the original refund reference'}"
                ". Depending on your bank, this typically reflects within 3–5 business days, and your account balance is settled."
            )
        elif action_result and action_result.get("ok") and action_status.lower() in {"refunded", "completed", "updated"}:
            comm_body = (
                f"we confirmed an accidental duplicate charge of {amount_str}"
                + (f" for order {order_id}" if order_id else "")
                + (f" (transaction {transaction_id})" if transaction_id else "")
                + f" and have reversed it under refund reference {refund_id or 'the refund reference on your account'}"
                ". Your account is settled in good standing, and the refund typically reflects within 3–5 business days."
            )
        elif remediation_needed and action_result and not action_result.get("ok"):
            comm_body = (
                "we were unable to complete the automatic refund from the records available, "
                "so we have escalated your case to our team for manual review within the SLA. "
                "We will follow up with you directly — no action is needed from you."
            )
        elif remediation_needed:
            comm_body = (
                f"we have reviewed your account for order {order_id or 'your recent order'} and flagged "
                f"{amount_str} for refund review by our team within the SLA."
            )
        else:
            comm_body = (
                "we have reviewed your request and our team will follow up shortly within the SLA. "
                "Thank you for bringing this to our attention."
            )

        template = (
            f"Hello {first_name},\n\n"
            f"Thank you for contacting {product} support — we are sorry for the trouble. "
            f"Regarding your complaint: \"{complaint}\"\n\n"
            f"Good news: {comm_body}\n\n"
            f"If you have any questions, simply reply to this message or contact us at {support_email}.\n\n"
            f"Warm regards,\nTroy — AI Resolution Specialist, {product}"
        )
        communication, llm_response = await self._maybe_llm_communication(
            complaint=complaint,
            category=category,
            customer=customer,
            action_result=action_result,
            fallback=template,
        )

        summary = (
            f"Complaint classified as '{category}' for {product}; "
            + ("duplicate charge refunded." if status == "resolved" and remediation_needed else
               "duplicate charge already refunded." if status == "already_refunded" else
               "dry-run only; no records changed." if status == "dry_run" else
               "refund pending approval." if status == "pending_approval" else
               "no database remediation required.")
        )
        report = {
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
            "actions_taken": [action_result] if action_result else [],
            "action_result": action_result,
            "next_steps": [
                "Refund confirmation sent to customer" if status == "resolved" and remediation_needed else "Customer follow-up within SLA",
                "Ticket closed as resolved" if status in {"resolved", "already_refunded"} else "Awaiting approval / dry-run confirmation",
            ],
            "customer_communication": communication,
        }

        payload: Dict[str, Any] = {
            "ok": True,
            "status": status,
            "product_name": product,
            "tenant": product,
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
            "action_taken": action_result,
            "refund_id": refund_id,
            "refund_amount": f"{amount_value:.2f}" if remediation_needed or is_already_refunded else None,
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
        if llm_response:
            payload["llm_response"] = llm_response

        # 7. Audit trail.
        try:
            self.adapter.audit_log(
                {
                    "event": "resolve_ticket",
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
                    "ok": True,
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
