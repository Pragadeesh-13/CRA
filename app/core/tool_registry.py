"""
Custom tool registry for Troy's plug-and-play SaaS layer.

Any SaaS can register tools — via decorator or programmatically — without
touching Troy's core:

    from app.core.tool_registry import TroyToolRegistry

    registry = TroyToolRegistry()

    @registry.tool(name="issue_refund", description="Issue a refund",
                   schema={"type": "object",
                           "required": ["transaction_id"],
                           "properties": {"transaction_id": {"type": "string"}}})
    def issue_refund(transaction_id: str, **kwargs):
        return {"ok": True, "transaction_id": transaction_id}

    result = registry.execute("issue_refund",
                              {"transaction_id": "TXN-1"},
                              idempotency_key="req-123", dry_run=False)

The registry guarantees:
  - parameter validation against an optional JSON-schema-like ``schema``,
  - idempotency-key tracking (replays return the cached result, no re-run),
  - dry-run execution (validates + audits without invoking the handler),
  - a full audit trail of every execution attempt.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Union

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(params: Mapping[str, Any]) -> str:
    try:
        payload = json.dumps(params, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = repr(params)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------


@dataclass
class TroyTool:
    """A single registered tool."""

    name: str
    func: Callable[..., Any]
    description: str = ""
    schema: Optional[Dict[str, Any]] = None
    dry_run_default: bool = False
    idempotent: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "schema": self.schema,
            "idempotent": self.idempotent,
        }


ToolDefinition = TroyTool
Tool = TroyTool


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_TYPE_CHECKS: Dict[str, Any] = {
    "string": lambda v: isinstance(v, str),
    "str": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "bool": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, (list, tuple)),
    "list": lambda v: isinstance(v, (list, tuple)),
    "object": lambda v: isinstance(v, Mapping),
    "dict": lambda v: isinstance(v, Mapping),
    "any": lambda v: True,
}


def validate_params(
    params: Mapping[str, Any],
    schema: Optional[Union[Dict[str, Any], Sequence[str], Mapping[str, Any]]],
) -> List[str]:
    """
    Validate ``params`` against ``schema``; return a list of error strings
    (empty when valid).

    Supported schema shapes:
      - JSON-schema-like: ``{"type": "object", "properties": {...}, "required": [...]}``
      - flat mapping: ``{"transaction_id": "string", "amount": "number"}``
        (values may also be Python types / ``{"type": "string"}`` dicts)
      - list of required names: ``["transaction_id", "amount"]``
    """
    errors: List[str] = []
    if not schema:
        return errors
    params = dict(params or {})

    if isinstance(schema, (list, tuple)):
        for field_name in schema:
            if str(field_name) not in params or params.get(str(field_name)) is None:
                errors.append(f"Missing required parameter: {field_name}")
        return errors

    if not isinstance(schema, Mapping):
        return errors

    # Flat mapping form: {name: "string" | type | {"type": ...}}
    if "properties" not in schema and "required" not in schema and "type" not in schema:
        for field_name, spec in schema.items():
            type_name: Optional[str] = None
            if isinstance(spec, str):
                type_name = spec.lower()
            elif isinstance(spec, type):
                check = _TYPE_CHECKS.get(spec.__name__.lower())
                if params.get(field_name) is not None and check is not None and not check(params[field_name]):
                    errors.append(f"Parameter '{field_name}' must be of type {spec.__name__}")
                continue
            elif isinstance(spec, Mapping) and "type" in spec:
                type_name = str(spec.get("type")).lower()
            if type_name:
                check = _TYPE_CHECKS.get(type_name)
                if check is not None and field_name in params and params[field_name] is not None and not check(params[field_name]):
                    errors.append(f"Parameter '{field_name}' must be of type {type_name}")
        return errors

    # JSON-schema-like form.
    required = schema.get("required") or []
    for field_name in required:
        if field_name not in params or params.get(field_name) is None:
            errors.append(f"Missing required parameter: {field_name}")

    properties = schema.get("properties") or {}
    for field_name, spec in properties.items():
        if field_name not in params or params[field_name] is None:
            continue
        if not isinstance(spec, Mapping):
            continue
        type_name = str(spec.get("type", "")).lower()
        check = _TYPE_CHECKS.get(type_name) if type_name else None
        if check is not None and not check(params[field_name]):
            errors.append(f"Parameter '{field_name}' must be of type {type_name}")
        enum = spec.get("enum")
        if enum is not None and params[field_name] not in enum:
            errors.append(f"Parameter '{field_name}' must be one of {list(enum)}")
    return errors


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TroyToolRegistry:
    """
    Registry of custom Troy tools with validation, idempotency, dry-run,
    and audit-trail support.

    Args:
        dry_run: registry-wide default for dry-run execution.
    """

    def __init__(self, dry_run: bool = False, **kwargs: Any) -> None:
        self._tools: Dict[str, TroyTool] = {}
        self._lock = threading.RLock()
        self.dry_run: bool = bool(dry_run or kwargs.get("default_dry_run", False))
        # idempotency_key -> {"tool": name, "fingerprint": ..., "result": ...}
        self._idempotency: Dict[str, Dict[str, Any]] = {}
        self._audit: List[Dict[str, Any]] = []
        # Public mirrors (commonly inspected by callers/tests).
        self.audit_trail: List[Dict[str, Any]] = self._audit
        self.audit_log_entries: List[Dict[str, Any]] = self._audit

    # -- registration --------------------------------------------------------

    def register(
        self,
        func: Optional[Callable[..., Any]] = None,
        *,
        name: Optional[str] = None,
        description: str = "",
        schema: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
        idempotent: bool = True,
        replace: bool = False,
    ) -> Callable[..., Any]:
        """Register ``func`` programmatically. Returns ``func`` unchanged."""
        if func is None:
            raise ValueError("register() requires a callable")
        tool_name = (name or getattr(func, "__name__", "") or "").strip()
        if not tool_name:
            raise ValueError("Tool name is required")
        with self._lock:
            if tool_name in self._tools and not replace:
                raise ValueError(f"Tool '{tool_name}' is already registered")
            self._tools[tool_name] = TroyTool(
                name=tool_name,
                func=func,
                description=description or (getattr(func, "__doc__", "") or "").strip(),
                schema=schema,
                dry_run_default=bool(dry_run),
                idempotent=bool(idempotent),
            )
        return func

    # Positional-friendly alias: register(name, func, ...)
    def register_tool(self, *args: Any, **kwargs: Any) -> Callable[..., Any]:
        """
        Register a tool. Accepts either::

            registry.register_tool(my_func, name="x", schema={...})
            registry.register_tool("x", my_func, description="...")
        """
        func: Optional[Callable[..., Any]] = kwargs.pop("func", None) or kwargs.pop("handler", None)
        name = kwargs.pop("name", None)
        for arg in args:
            if callable(arg) and func is None:
                func = arg
            elif isinstance(arg, str) and name is None:
                name = arg
        if func is None:
            raise ValueError("register_tool() requires a callable")
        return self.register(func, name=name, **kwargs)

    add_tool = register_tool

    def tool(
        self,
        func: Optional[Callable[..., Any]] = None,
        *,
        name: Optional[str] = None,
        description: str = "",
        schema: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
        idempotent: bool = True,
    ) -> Any:
        """
        Decorator form::

            @registry.tool(name="x", schema={...})
            def x(...): ...

        Also usable bare (``@registry.tool``) or with a positional name
        (``@registry.tool("x")``).
        """
        # Bare decorator: @registry.tool  (func passed positionally, name unset)
        if func is not None and callable(func) and name is None and not description and schema is None:
            return self.register(func)
        # Positional name: @registry.tool("x")
        if isinstance(func, str) and name is None:
            name, func = func, None  # type: ignore[assignment]
        if func is not None:
            return self.register(func, name=name, description=description, schema=schema, dry_run=dry_run, idempotent=idempotent)

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            return self.register(fn, name=name, description=description, schema=schema, dry_run=dry_run, idempotent=idempotent)

        return decorator

    def unregister(self, name: str) -> bool:
        with self._lock:
            return self._tools.pop(name, None) is not None

    # -- introspection ---------------------------------------------------------

    def get_tool(self, name: str) -> Optional[TroyTool]:
        return self._tools.get(name)

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def list_tools(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [tool.to_dict() for tool in self._tools.values()]

    def tool_names(self) -> List[str]:
        with self._lock:
            return list(self._tools.keys())

    @property
    def tools(self) -> Dict[str, TroyTool]:
        return dict(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    # -- audit -----------------------------------------------------------------

    def _append_audit(self, entry: Dict[str, Any]) -> None:
        stamped = {"timestamp": _utcnow_iso(), **entry}
        with self._lock:
            self._audit.append(stamped)

    def get_audit_trail(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self._audit]

    # -- execution -------------------------------------------------------------

    def execute(
        self,
        tool_name: str,
        params: Optional[Mapping[str, Any]] = None,
        *,
        idempotency_key: Optional[str] = None,
        idempotent_key: Optional[str] = None,
        dry_run: Optional[bool] = None,
        raise_on_error: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Execute a registered tool with validation, idempotency, and
        dry-run support. Always returns a result dict::

            {"ok": bool, "status": ..., "tool": name, "result": ...,
             "dry_run": bool, "idempotency_key": ..., ...}
        """
        params_dict = dict(params or {})
        params_dict.update(kwargs)
        key = idempotency_key or idempotent_key
        tool = self._tools.get(tool_name)

        def _fail(message: str, **extra: Any) -> Dict[str, Any]:
            result = {
                "ok": False,
                "status": "Failed",
                "tool": tool_name,
                "error": message,
                "dry_run": bool(dry_run),
                "idempotency_key": key,
                **extra,
            }
            self._append_audit(
                {"event": "tool_execute", "tool": tool_name, "params": params_dict,
                 "idempotency_key": key, "dry_run": bool(dry_run), "ok": False, "error": message}
            )
            if raise_on_error:
                raise ValueError(message)
            return result

        if tool is None:
            return _fail(f"Unknown tool '{tool_name}'")

        effective_dry_run = bool(dry_run) if dry_run is not None else bool(self.dry_run or tool.dry_run_default)

        # Idempotency replay (only for real, non-dry-run executions).
        if key and tool.idempotent and not effective_dry_run:
            with self._lock:
                prior = self._idempotency.get(str(key))
            if prior is not None and prior.get("tool") == tool_name:
                replay = copy.deepcopy(prior["result"])
                replay["idempotent_replay"] = True
                replay["from_cache"] = True
                replay["is_idempotent_replay"] = True
                self._append_audit(
                    {"event": "tool_execute", "tool": tool_name, "params": params_dict,
                     "idempotency_key": key, "dry_run": False, "ok": bool(replay.get("ok", True)),
                     "idempotent_replay": True}
                )
                return replay
            if prior is not None and prior.get("tool") != tool_name:
                return _fail(f"Idempotency key '{key}' was already used for tool '{prior.get('tool')}'")

        # Parameter validation (runs in dry-run too).
        errors = validate_params(params_dict, tool.schema)
        if errors:
            return _fail("; ".join(errors), validation_errors=errors)

        if effective_dry_run:
            result = {
                "ok": True,
                "status": "dry_run",
                "tool": tool_name,
                "dry_run": True,
                "idempotency_key": key,
                "params": params_dict,
                "result": None,
            }
            self._append_audit(
                {"event": "tool_execute", "tool": tool_name, "params": params_dict,
                 "idempotency_key": key, "dry_run": True, "ok": True}
            )
            return result

        # Invoke the handler: prefer **params kwargs, fall back to a single dict.
        try:
            sig = inspect.signature(tool.func)
            positional = [p for p in sig.parameters.values()
                          if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)]
            accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
            if accepts_kwargs and (not positional or set(params_dict) - {p.name for p in positional}):
                raw = tool.func(**params_dict)
            elif positional:
                bound: List[Any] = []
                for p in positional:
                    if p.name in params_dict:
                        bound.append(params_dict[p.name])
                    elif p.default is not inspect.Parameter.empty:
                        bound.append(p.default)
                    else:
                        bound.append(None)
                raw = tool.func(*bound)
            else:
                raw = tool.func(**params_dict)
        except Exception as exc:
            logger.warning("Tool '%s' failed: %s", tool_name, exc)
            return _fail(str(exc))

        if isinstance(raw, Mapping):
            result = dict(raw)
            result.setdefault("ok", True)
        else:
            result = {"ok": True, "result": raw}
        result.setdefault("status", "Completed" if result.get("ok", True) else "Failed")
        result["tool"] = tool_name
        result["dry_run"] = False
        if key:
            result["idempotency_key"] = key

        # Cache for idempotent replay.
        if key and tool.idempotent:
            with self._lock:
                self._idempotency[str(key)] = {
                    "tool": tool_name,
                    "fingerprint": _fingerprint(params_dict),
                    "result": copy.deepcopy(result),
                }

        self._append_audit(
            {"event": "tool_execute", "tool": tool_name, "params": params_dict,
             "idempotency_key": key, "dry_run": False, "ok": bool(result.get("ok", True))}
        )
        return result

    # Aliases
    call = execute
    invoke = execute
    run = execute
    execute_tool = execute


# Convenience alias
ToolRegistry = TroyToolRegistry


__all__ = [
    "TroyToolRegistry",
    "ToolRegistry",
    "TroyTool",
    "ToolDefinition",
    "Tool",
    "validate_params",
]
