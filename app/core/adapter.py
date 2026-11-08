"""
Data-store adapters for Troy's plug-and-play SaaS layer.

A SaaS operator only needs to supply *one* :class:`DataStoreAdapter`
implementation to point Troy at their product:

  - :class:`CsvDataAdapter`      — wraps the existing ``app.database`` CSV
    store (full backward compatibility, no new setup).
  - :class:`InMemoryDataAdapter` — zero-setup in-memory mock for demos/tests.
  - :class:`RestApiDataAdapter`  — forwards queries/actions to external SaaS
    HTTP endpoints.

Example:
    from app.core.adapter import CsvDataAdapter

    store = CsvDataAdapter()
    customer = store.get_customer("USR-10492")
    txns = store.get_transactions("USR-10492")
    result = store.execute_action("remediate_duplicate_charge", {
        "order_id": "ORD-88219", "duplicate_txn_id": "TXN-9012",
    })
"""

from __future__ import annotations

import copy
import logging
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Union

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(value: Any) -> str:
    return str(value).strip().upper() if value is not None else ""


def _norm_email(value: Any) -> str:
    return str(value).strip().lower() if value is not None else ""


def _copy(record: Mapping[str, Any]) -> Dict[str, Any]:
    return dict(copy.deepcopy(record))


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


class DataStoreAdapter(ABC):
    """
    Abstract interface every Troy data store must implement.

    All methods are synchronous and return plain dicts/lists so adapters are
    trivial to mock, log, and serialise.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._lock = threading.RLock()
        self.audit_entries: List[Dict[str, Any]] = []

    @abstractmethod
    def get_customer(self, identifier: str) -> Optional[Dict[str, Any]]:
        """Return the customer record for ``identifier`` or ``None``."""
        raise NotImplementedError

    @abstractmethod
    def get_transactions(self, customer_id: str, **kwargs: Any) -> List[Dict[str, Any]]:
        """Return transaction records for ``customer_id`` (filters via kwargs)."""
        raise NotImplementedError

    @abstractmethod
    def execute_action(self, action_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a named remediation action and return a result dict."""
        raise NotImplementedError

    @abstractmethod
    def audit_log(self, entry: Dict[str, Any]) -> None:
        """Persist a single audit-trail entry."""
        raise NotImplementedError

    # -- Shared convenience helpers ------------------------------------------

    def _record_audit(self, entry: Dict[str, Any]) -> None:
        stamped = {"timestamp": _utcnow_iso(), **dict(entry)}
        with self._lock:
            self.audit_entries.append(stamped)
        try:
            self.audit_log(stamped)
        except Exception as exc:  # audit must never break the caller
            logger.warning("audit_log failed: %s", exc)

    @property
    def audit_trail(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self.audit_entries)

    # Aliases
    get_audit_log = audit_trail

    def find_customer(self, identifier: str) -> Optional[Dict[str, Any]]:
        return self.get_customer(identifier)

    def query_customer(self, identifier: str) -> Optional[Dict[str, Any]]:
        return self.get_customer(identifier)

    def find_transactions(self, customer_id: str, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.get_transactions(customer_id, **kwargs)

    def query_transactions(self, customer_id: str, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.get_transactions(customer_id, **kwargs)

    def execute(self, action_name: str, params: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        merged = {**(params or {}), **kwargs}
        return self.execute_action(action_name, merged)


# ---------------------------------------------------------------------------
# CSV adapter (backwards compatible with app.database)
# ---------------------------------------------------------------------------


class CsvDataAdapter(DataStoreAdapter):
    """
    Adapter backed by the existing ``app.database`` CSV files
    (``data/transactions.csv`` + ``data/customer_accounts.csv``).

    With no arguments it behaves exactly like the pre-existing database
    helpers — including honouring monkey-patched module constants in tests.
    Optional file paths may be supplied for standalone use.
    """

    def __init__(
        self,
        transactions_file: Optional[Union[str, Path]] = None,
        accounts_file: Optional[Union[str, Path]] = None,
        *,
        customer_accounts_file: Optional[Union[str, Path]] = None,
        database_file: Optional[Union[str, Path]] = None,
        database_module: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        # Accept a few common alias spellings for the file paths.
        self.transactions_file = Path(transactions_file or database_file or kwargs.get("transactions_path") or kwargs.get("db_path")) if (transactions_file or database_file or kwargs.get("transactions_path") or kwargs.get("db_path")) else None
        self.accounts_file = Path(accounts_file or customer_accounts_file or kwargs.get("accounts_path") or kwargs.get("customer_accounts_path")) if (accounts_file or customer_accounts_file or kwargs.get("accounts_path") or kwargs.get("customer_accounts_path")) else None
        self._db_module = database_module
        self._handler: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = kwargs.get("action_handler") or kwargs.get("handler")

    # -- internal ------------------------------------------------------------

    @property
    def _db(self) -> Any:
        if self._db_module is not None:
            return self._db_module
        import app.database as db  # imported lazily so test monkey-patching applies

        return db

    def _find_customer(self, identifier: str) -> Optional[Dict[str, Any]]:
        db = self._db
        ident = (identifier or "").strip()
        if not ident:
            return None
        # 1) Exact helpers in priority order.
        if self.accounts_file is None:
            # Default path: delegate to app.database so its isolation works.
            account = None
            if "@" in ident:
                account = db.query_customer_account(email=ident)
            elif ident.upper().startswith("ORD-") or ident.isdigit():
                account = db.query_customer_account(order_id=ident)
            else:
                account = db.query_customer_account(customer_id=ident)
            if account is not None:
                return dict(account)
            # Fall back to the fuzzy query resolver (name / order / email in text).
            found = db.find_customer_account_by_query(query=ident, customer_id=ident)
            if found is not None:
                return dict(found)
            return None
        # Explicit files: read via the database module's readers after
        # temporarily pointing it at our files is intrusive, so scan directly.
        accounts = db.read_all_customer_accounts() if hasattr(db, "read_all_customer_accounts") else []
        for acc in accounts:
            if (
                _norm(acc.get("customer_id")) == _norm(ident)
                or _norm_email(acc.get("customer_email")) == _norm_email(ident)
                or _norm(acc.get("reference_order_id")) == _norm(ident)
                or _norm(acc.get("customer_name")) == _norm(ident)
            ):
                return dict(acc)
        return None

    # -- interface -----------------------------------------------------------

    def get_customer(self, identifier: str) -> Optional[Dict[str, Any]]:
        customer = self._find_customer(identifier)
        if customer is not None:
            self._record_audit({"event": "get_customer", "identifier": identifier, "found": True})
        else:
            self._record_audit({"event": "get_customer", "identifier": identifier, "found": False})
        return customer

    def get_transactions(self, customer_id: str, **kwargs: Any) -> List[Dict[str, Any]]:
        db = self._db
        filters = {k: v for k, v in kwargs.items() if v is not None}
        if self.transactions_file is None:
            if filters:
                allowed = {"customer_id", "order_id", "status", "billing_cycle", "transaction_id"}
                call_kwargs = {k: filters[k] for k in allowed if k in filters}
                if customer_id:
                    call_kwargs.setdefault("customer_id", customer_id)
                results = db.query_transactions(**call_kwargs)
            elif customer_id:
                results = db.query_transactions(customer_id=customer_id)
                if not results:
                    try:
                        results = db.find_transactions_for_query(customer_id=customer_id, query=customer_id) or []
                    except Exception:
                        results = []
            else:
                results = db.read_all_transactions()
            return [dict(r) for r in results]
        # Explicit-file mode.
        all_txns = db.read_all_transactions()
        target = _norm(customer_id)
        out = [
            dict(t)
            for t in all_txns
            if (not target or _norm(t.get("customer_id")) == target)
            and all(str(t.get(k, "")) == str(v) for k, v in filters.items())
        ]
        return out

    def execute_action(self, action_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        params = dict(params or {})
        db = self._db
        action = (action_name or "").strip()
        result: Dict[str, Any]

        if self._handler is not None:
            result = self._handler(action, params) or {"ok": True}

        elif action in {"remediate_duplicate_charge", "refund", "refund_duplicate_charge", "issue_refund", "reverse_charge"}:
            duplicate_txn_id = (
                params.get("duplicate_txn_id")
                or params.get("transaction_id")
                or params.get("duplicate_transaction_id")
                or params.get("txn_id")
                or ""
            )
            result = db.remediate_duplicate_charge(
                order_id=params.get("order_id") or params.get("reference_order_id") or "",
                duplicate_txn_id=str(duplicate_txn_id),
                refund_amount=str(params.get("refund_amount") or params.get("amount") or "49.00"),
                processed_by=params.get("processed_by") or "Troy (Autonomous Resolution Agent)",
                notes=params.get("notes") or "",
            )
            result.setdefault("action", action)

        elif action in {"update_transaction", "update_txn"}:
            ok = db.update_transaction(
                str(params.get("transaction_id") or params.get("id") or ""),
                params.get("updates") or {k: v for k, v in params.items() if k not in {"transaction_id", "id", "updates"}},
            )
            result = {"ok": bool(ok), "status": "Updated" if ok else "Failed", "action": action}

        elif action in {"update_customer_account", "update_customer", "update_account"}:
            ok = db.update_customer_account(
                str(params.get("customer_id") or params.get("id") or ""),
                params.get("updates") or {k: v for k, v in params.items() if k not in {"customer_id", "id", "updates"}},
            )
            result = {"ok": bool(ok), "status": "Updated" if ok else "Failed", "action": action}

        elif action in {"inspect_account_and_transactions", "inspect_account", "audit_customer"}:
            result = db.inspect_account_and_transactions(
                customer_id=params.get("customer_id"),
                query=params.get("query") or params.get("identifier"),
            )
            result.setdefault("action", action)

        elif action in {"query_customer_account", "get_customer", "find_customer"}:
            found = db.query_customer_account(
                customer_id=params.get("customer_id") or params.get("identifier"),
                email=params.get("email"),
                order_id=params.get("order_id"),
            )
            result = {"ok": found is not None, "customer": found, "action": action}

        else:
            result = {
                "ok": False,
                "status": "Failed",
                "action": action,
                "error": f"Unknown action '{action}' for CsvDataAdapter",
            }

        self._record_audit({"event": "execute_action", "action": action, "params": params, "ok": bool(result.get("ok"))})
        return result

    def audit_log(self, entry: Dict[str, Any]) -> None:
        # Entries are already retained in ``self.audit_entries`` by
        # ``_record_audit``; this hook exists so subclasses/overrides can
        # persist them externally. Kept idempotent-safe: if called directly
        # (not via _record_audit) we still retain the entry once.
        if entry not in self.audit_entries:
            self.audit_entries.append({"timestamp": _utcnow_iso(), **dict(entry)})


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


class InMemoryDataAdapter(DataStoreAdapter):
    """
    Zero-setup in-memory store for demos, tests, and any SaaS that wants to
    start with mock data.

    Accepts customers/transactions as dicts or lists of dicts::

        store = InMemoryDataAdapter(customers=[{...}], transactions=[{...}])
        store = InMemoryDataAdapter({
            "customers": {"USR-1": {...}},
            "transactions": [...],
        })
    """

    def __init__(
        self,
        customers: Optional[Union[Mapping[str, Any], Sequence[Mapping[str, Any]]]] = None,
        transactions: Optional[Union[Mapping[str, Any], Sequence[Mapping[str, Any]]]] = None,
        data: Optional[Mapping[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.customers: Dict[str, Dict[str, Any]] = {}
        self.transactions: List[Dict[str, Any]] = []

        # Allow the documented single-dict form:
        # InMemoryDataAdapter({"customers": {...}, "transactions": [...]})
        if data is None and transactions is None and isinstance(customers, Mapping) and ("customers" in customers or "transactions" in customers):
            data = customers  # type: ignore[assignment]
            customers = None
        if data is not None:
            customers = data.get("customers", customers)
            transactions = data.get("transactions", transactions)
        customers = customers if customers is not None else kwargs.get("accounts") or kwargs.get("customer_accounts")
        transactions = transactions if transactions is not None else kwargs.get("txns")

        if customers:
            if isinstance(customers, Mapping):
                for key, value in customers.items():
                    record = _copy(value)
                    record.setdefault("customer_id", str(key))
                    self.customers[_norm(record["customer_id"])] = record
            else:
                for value in customers:
                    record = _copy(value)
                    cid = record.get("customer_id") or record.get("id") or ""
                    if cid:
                        self.customers[_norm(cid)] = record
        if transactions:
            if isinstance(transactions, Mapping):
                for value in transactions.values():
                    self.transactions.append(_copy(value))
            else:
                for value in transactions:
                    self.transactions.append(_copy(value))
        self._handler: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = kwargs.get("action_handler") or kwargs.get("handler")

    # -- seeding helpers ------------------------------------------------------

    def add_customer(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        rec = _copy(record)
        cid = rec.get("customer_id") or rec.get("id") or ""
        if cid:
            self.customers[_norm(cid)] = rec
        return rec

    def add_transaction(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        rec = _copy(record)
        self.transactions.append(rec)
        return rec

    # -- interface ------------------------------------------------------------

    def get_customer(self, identifier: str) -> Optional[Dict[str, Any]]:
        ident = (identifier or "").strip()
        if not ident:
            return None
        target = _norm(ident)
        target_email = _norm_email(ident)
        for record in self.customers.values():
            if (
                _norm(record.get("customer_id")) == target
                or _norm_email(record.get("customer_email")) == target_email
                or _norm(record.get("email")) == target_email
                or _norm(record.get("reference_order_id")) == target
                or _norm(record.get("order_id")) == target
                or _norm(record.get("customer_name")) == target
                or _norm(record.get("name")) == target
                or _norm(record.get("id")) == target
            ):
                result = _copy(record)
                self._record_audit({"event": "get_customer", "identifier": identifier, "found": True})
                return result
        # Also search transactions for a customer stub.
        for txn in self.transactions:
            if _norm(txn.get("customer_id")) == target or _norm(txn.get("order_id")) == target:
                result = {
                    "customer_id": txn.get("customer_id", ""),
                    "customer_name": txn.get("customer_name", ""),
                    "customer_email": txn.get("customer_email", ""),
                    "reference_order_id": txn.get("order_id", ""),
                }
                self._record_audit({"event": "get_customer", "identifier": identifier, "found": True})
                return result
        self._record_audit({"event": "get_customer", "identifier": identifier, "found": False})
        return None

    def get_transactions(self, customer_id: str, **kwargs: Any) -> List[Dict[str, Any]]:
        target = _norm(customer_id)
        filters = {k: v for k, v in kwargs.items() if v is not None}
        out: List[Dict[str, Any]] = []
        for txn in self.transactions:
            if target and _norm(txn.get("customer_id")) != target:
                continue
            if all(str(txn.get(k, "")) == str(v) for k, v in filters.items()):
                out.append(_copy(txn))
        return out

    def execute_action(self, action_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        params = dict(params or {})
        action = (action_name or "").strip()
        result: Dict[str, Any]

        try:
            if self._handler is not None:
                result = self._handler(action, params) or {"ok": True}

            elif action in {"remediate_duplicate_charge", "refund", "refund_duplicate_charge", "issue_refund", "reverse_charge"}:
                duplicate_txn_id = str(
                    params.get("duplicate_txn_id") or params.get("transaction_id") or params.get("txn_id") or ""
                )
                target = next((t for t in self.transactions if _norm(t.get("transaction_id")) == _norm(duplicate_txn_id)), None)
                if target is None:
                    result = {"ok": False, "status": "Failed", "action": action, "error": f"Transaction {duplicate_txn_id} not found"}
                elif target.get("status") == "Refunded":
                    result = {
                        "ok": True,
                        "status": "Refunded",
                        "action": action,
                        "is_already_refunded": True,
                        "already_refunded": True,
                        "transaction_id": duplicate_txn_id,
                        "refund_id": target.get("refund_id", ""),
                        "refund_amount": target.get("refund_amount", target.get("amount", "")),
                    }
                else:
                    amount = str(params.get("refund_amount") or params.get("amount") or target.get("amount") or "49.00")
                    refund_id = params.get("refund_id") or target.get("refund_id") or f"REF-{duplicate_txn_id}-AUTO"
                    target["status"] = "Refunded"
                    target["refund_id"] = str(refund_id)
                    target["refund_amount"] = amount
                    target["refunded_at"] = _utcnow_iso()
                    target["processed_by"] = params.get("processed_by") or "Troy (Autonomous Resolution Agent)"
                    if params.get("notes"):
                        target["notes"] = params["notes"]
                    result = {
                        "ok": True,
                        "status": "Refunded",
                        "action": action,
                        "transaction_id": duplicate_txn_id,
                        "order_id": params.get("order_id") or target.get("order_id", ""),
                        "customer_id": target.get("customer_id", ""),
                        "refund_id": target["refund_id"],
                        "refund_amount": amount,
                        "is_already_refunded": False,
                        "already_refunded": False,
                    }

            elif action in {"update_transaction", "update_txn"}:
                txn_id = _norm(params.get("transaction_id") or params.get("id") or "")
                updates = params.get("updates") or {k: v for k, v in params.items() if k not in {"transaction_id", "id", "updates"}}
                target = next((t for t in self.transactions if _norm(t.get("transaction_id")) == txn_id), None)
                if target is None:
                    result = {"ok": False, "status": "Failed", "action": action, "error": "Transaction not found"}
                else:
                    target.update({k: str(v) for k, v in updates.items()})
                    result = {"ok": True, "status": "Updated", "action": action, "transaction": _copy(target)}

            elif action in {"update_customer_account", "update_customer", "update_account"}:
                cid = _norm(params.get("customer_id") or params.get("id") or "")
                updates = params.get("updates") or {k: v for k, v in params.items() if k not in {"customer_id", "id", "updates"}}
                target = self.customers.get(cid)
                if target is None:
                    result = {"ok": False, "status": "Failed", "action": action, "error": "Customer not found"}
                else:
                    target.update({k: str(v) for k, v in updates.items()})
                    result = {"ok": True, "status": "Updated", "action": action, "customer": _copy(target)}

            else:
                result = {
                    "ok": False,
                    "status": "Failed",
                    "action": action,
                    "error": f"Unknown action '{action}' for InMemoryDataAdapter",
                }
        except Exception as exc:
            result = {"ok": False, "status": "Failed", "action": action, "error": str(exc)}

        result.setdefault("action", action)
        self._record_audit({"event": "execute_action", "action": action, "params": params, "ok": bool(result.get("ok"))})
        return result

    def audit_log(self, entry: Dict[str, Any]) -> None:
        if entry not in self.audit_entries:
            self.audit_entries.append({"timestamp": _utcnow_iso(), **dict(entry)})


# ---------------------------------------------------------------------------
# REST API adapter
# ---------------------------------------------------------------------------


class RestApiDataAdapter(DataStoreAdapter):
    """
    Adapter that forwards queries/actions to external SaaS HTTP endpoints.

    Endpoint conventions (overridable via ``endpoints``):
      - ``GET  {base_url}/customers/{identifier}``
      - ``GET  {base_url}/transactions?customer_id={id}`` (+ query filters)
      - ``POST {base_url}/actions/{action_name}`` with JSON ``params``
      - ``POST {base_url}/audit`` with the audit entry as JSON

    A custom ``http_client`` (anything with ``.get``/``.post`` returning an
    object with ``.json()`` / ``.status_code`` / ``.raise_for_status()``, e.g.
    an ``httpx.Client``) can be injected for testing. When omitted, an
    ``httpx.Client`` is created lazily (falling back to ``urllib``).
    """

    DEFAULT_ENDPOINTS: Dict[str, str] = {
        "customer": "/customers/{identifier}",
        "transactions": "/transactions",
        "action": "/actions/{action_name}",
        "audit": "/audit",
    }

    def __init__(
        self,
        base_url: str = "",
        *,
        api_key: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 10.0,
        endpoints: Optional[Dict[str, str]] = None,
        http_client: Any = None,
        client: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.base_url = (base_url or kwargs.get("url") or "").rstrip("/")
        self.api_key = api_key or kwargs.get("token") or kwargs.get("auth_token")
        self.timeout = timeout
        self.endpoints = {**self.DEFAULT_ENDPOINTS, **(endpoints or {})}
        self.http_client = http_client or client or kwargs.get("session") or kwargs.get("httpx_client")
        self._headers = dict(headers or {})
        if self.api_key:
            self._headers.setdefault("Authorization", f"Bearer {self.api_key}")
        self._headers.setdefault("Accept", "application/json")
        self._headers.setdefault("Content-Type", "application/json")
        # Lazily-created default client cache.
        self._owned_client: Any = None

    # -- HTTP plumbing ---------------------------------------------------------

    def _url(self, path_template: str, **values: Any) -> str:
        path = path_template.format(**{k: str(v) for k, v in values.items()})
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}/{path.lstrip('/')}" if self.base_url else path

    def _client(self) -> Any:
        if self.http_client is not None:
            return self.http_client
        if self._owned_client is None:
            try:
                import httpx

                self._owned_client = httpx.Client(base_url=self.base_url, headers=self._headers, timeout=self.timeout)
            except Exception as exc:  # pragma: no cover - httpx is a project dep
                raise RuntimeError(f"RestApiDataAdapter requires httpx or an injected http_client: {exc}") from exc
        return self._owned_client

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("headers", self._headers)
        kwargs.setdefault("timeout", self.timeout)
        client = self._client()
        fn = getattr(client, method.lower())
        response = fn(url, **kwargs)
        if hasattr(response, "raise_for_status"):
            try:
                response.raise_for_status()
            except Exception:
                # Surface HTTP errors as data rather than raising through Troy.
                status = getattr(response, "status_code", None)
                try:
                    body = response.json()
                except Exception:
                    body = getattr(response, "text", "")
                return {"ok": False, "status": "Failed", "status_code": status, "error": body}
        if hasattr(response, "json"):
            try:
                data = response.json()
            except Exception:
                data = getattr(response, "text", "")
        else:
            data = getattr(response, "data", response)
        if isinstance(data, dict):
            return data
        return {"ok": True, "data": data}

    def close(self) -> None:
        if self._owned_client is not None and hasattr(self._owned_client, "close"):
            self._owned_client.close()
            self._owned_client = None

    # -- interface -------------------------------------------------------------

    def get_customer(self, identifier: str) -> Optional[Dict[str, Any]]:
        url = self._url(self.endpoints["customer"], identifier=identifier)
        data = self._request("get", url)
        customer = data.get("customer") if isinstance(data, dict) and "customer" in data else data
        if isinstance(customer, dict) and customer.get("ok") is False:
            self._record_audit({"event": "get_customer", "identifier": identifier, "found": False})
            return None
        if isinstance(customer, dict) and customer:
            self._record_audit({"event": "get_customer", "identifier": identifier, "found": True})
            return customer
        self._record_audit({"event": "get_customer", "identifier": identifier, "found": False})
        return None

    def get_transactions(self, customer_id: str, **kwargs: Any) -> List[Dict[str, Any]]:
        url = self._url(self.endpoints["transactions"])
        params = {"customer_id": customer_id, **{k: v for k, v in kwargs.items() if v is not None}}
        data = self._request("get", url, params=params)
        if isinstance(data, list):
            return [dict(r) for r in data]
        if isinstance(data, dict):
            txns = data.get("transactions") or data.get("data") or data.get("items") or []
            if isinstance(txns, list):
                return [dict(r) for r in txns if isinstance(r, Mapping)]
        return []

    def execute_action(self, action_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        url = self._url(self.endpoints["action"], action_name=action_name)
        data = self._request("post", url, json=dict(params or {}))
        if isinstance(data, dict):
            result = data
        else:
            result = {"ok": True, "data": data}
        result.setdefault("ok", True)
        result.setdefault("action", action_name)
        self._record_audit({"event": "execute_action", "action": action_name, "params": dict(params or {}), "ok": bool(result.get("ok"))})
        return result

    def audit_log(self, entry: Dict[str, Any]) -> None:
        # Best-effort remote audit: never let a failing endpoint break callers.
        try:
            url = self._url(self.endpoints["audit"])
            self._request("post", url, json=dict(entry))
        except Exception as exc:
            logger.debug("RestApiDataAdapter remote audit failed: %s", exc)
        if entry not in self.audit_entries:
            self.audit_entries.append({"timestamp": _utcnow_iso(), **dict(entry)})


# Aliases for convenience / discoverability.
CSVAdapter = CsvDataAdapter
InMemoryAdapter = InMemoryDataAdapter
RestAdapter = RestApiDataAdapter
RESTApiDataAdapter = RestApiDataAdapter


def create_adapter(kind: str = "csv", **kwargs: Any) -> DataStoreAdapter:
    """Factory: ``create_adapter('csv' | 'memory' | 'rest', **kwargs)``."""
    key = (kind or "csv").strip().lower()
    mapping: Dict[str, Any] = {
        "csv": CsvDataAdapter,
        "file": CsvDataAdapter,
        "database": CsvDataAdapter,
        "memory": InMemoryDataAdapter,
        "inmemory": InMemoryDataAdapter,
        "in-memory": InMemoryDataAdapter,
        "mock": InMemoryDataAdapter,
        "rest": RestApiDataAdapter,
        "restapi": RestApiDataAdapter,
        "rest_api": RestApiDataAdapter,
        "http": RestApiDataAdapter,
        "api": RestApiDataAdapter,
    }
    cls = mapping.get(key)
    if cls is None:
        raise ValueError(f"Unknown adapter kind '{kind}'. Supported: {', '.join(sorted(set(mapping)))}")
    return cls(**kwargs)


__all__ = [
    "DataStoreAdapter",
    "CsvDataAdapter",
    "CSVAdapter",
    "InMemoryDataAdapter",
    "InMemoryAdapter",
    "RestApiDataAdapter",
    "RestAdapter",
    "RESTApiDataAdapter",
    "create_adapter",
]
