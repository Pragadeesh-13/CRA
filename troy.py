"""
Troy — a clean, plug-and-play, open-source complaint-resolution tool.

Troy is a single, self-contained agent: you create one agent and call
it. No registries or per-customer setup are required, and it works
offline out of the box with no API keys.

Python usage::

    import troy

    # One-shot, zero setup:
    result = troy.resolve("I was charged twice for order ORD-88219")
    print(result["ok"], result["customer_communication"])

    # Or keep an agent instance around:
    agent = troy.Troy(product_name='MyApp')

    @agent.tool(name="reset_quota", description="Reset a customer's quota")
    def reset_quota(customer_id: str = "", **kwargs):
        return {"ok": True, "customer_id": customer_id, "quota": "reset"}

    result = agent.resolve("How do I reset my quota?")
    print(result["ok"])

    # Bring your own data store / config:
    agent = troy.Troy(
        config={"product_name": "My Product", "domain": "example.com"},
        adapter=troy.InMemoryDataAdapter(customers=[...], transactions=[...]),
    )

Command-line usage::

    python troy.py "I was charged twice for order ORD-88219"
    python troy.py "How do I export my data?" --json
    python troy.py "I was charged twice" --dry-run --json
    python troy.py "I was charged twice" --auto-execute --json
    python troy.py --version
    python troy.py --help
    python troy.py --serve            # FastAPI app on port 3000
    python troy.py --serve --port 8000

Canonical exports: ``Troy``, ``resolve``, ``tool``, ``load_config``,
``CsvDataAdapter``, ``InMemoryDataAdapter``, ``RestApiDataAdapter``.

See ``docs/ARCHITECTURE.md`` and ``docs/MODULES.md`` for the full
module reference.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Allow ``python troy.py`` / ``import troy`` from any working directory:
# the repository root (this file's directory) must be importable so that
# ``app.*`` resolves.
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.core.adapter import CsvDataAdapter, InMemoryDataAdapter, RestApiDataAdapter  # noqa: E402
from app.core.saas_config import SaaSConfig, load_saas_config  # noqa: E402
from app.sdk.client import TroyClient  # noqa: E402

try:  # pragma: no cover - trivial version probe
    from app import __version__ as _APP_VERSION
except Exception:  # pragma: no cover
    _APP_VERSION = "1.0.0"

__version__ = str(_APP_VERSION or "1.0.0")
__all__ = [
    "Troy",
    "TroyClient",
    "resolve",
    "resolve_async",
    "tool",
    "register_tool",
    "execute_tool",
    "load_config",
    "load_saas_config",
    "SaaSConfig",
    "CsvDataAdapter",
    "InMemoryDataAdapter",
    "RestApiDataAdapter",
    "get_default_agent",
    "default_agent",
    "serve",
    "main",
    "__version__",
]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def load_config(source: Any = None, **overrides: Any):
    """
    Load a Troy config from a dict, JSON/YAML string, file path, or
    ``SaaSConfig`` — or return the default config when ``source`` is None.
    """
    return load_saas_config(source, **overrides)


# ---------------------------------------------------------------------------
# Troy agent (single open-source agent)
# ---------------------------------------------------------------------------


class Troy(TroyClient):
    """
    The Troy complaint-resolution agent.

    A plain :class:`~app.sdk.client.TroyClient` with a synchronous,
    ergonomic front door for open-source use::

        agent = Troy()                                   # zero setup
        agent = Troy(product_name="MyApp")               # custom product
        agent = Troy(config={"product_name": "My Shop"}) # custom config
        agent = Troy(adapter=InMemoryDataAdapter(...))   # custom data

        result = agent.resolve("I was charged twice ...")        # sync
        result = await agent.resolve_async("...")                # async
        result = await agent.resolve_ticket("...")               # async (SDK)

    Custom actions are added with the ``@agent.tool`` decorator (or
    ``agent.register_tool(...)``) and run via ``agent.execute_tool(...)``.
    """

    def __init__(
        self,
        config: Any = None,
        adapter: Any = None,
        tool_registry: Any = None,
        driver: Any = None,
        *,
        product_name: Optional[str] = None,
        name: Optional[str] = None,
        domain: Optional[str] = None,
        support_email: Optional[str] = None,
        email: Optional[str] = None,
        branding: Any = None,
        categories: Any = None,
        policies: Any = None,
        entity_patterns: Any = None,
        **kwargs: Any,
    ) -> None:
        # Ergonomic open-source form: Troy(product_name="MyApp") should
        # configure the agent without requiring a nested config dict.
        config_overrides: Dict[str, Any] = {}
        if product_name is not None:
            config_overrides["product_name"] = product_name
        if name is not None:
            config_overrides.setdefault("product_name", name)
        if domain is not None:
            config_overrides["domain"] = domain
        if support_email is not None:
            config_overrides["support_email"] = support_email
        if email is not None:
            config_overrides.setdefault("support_email", email)
        if branding is not None:
            config_overrides["branding"] = branding
        if categories is not None:
            config_overrides["categories"] = categories
        if policies is not None:
            config_overrides["policies"] = policies
        if entity_patterns is not None:
            config_overrides["entity_patterns"] = entity_patterns
        # Also accept config keys passed as plain kwargs for convenience.
        for key in ("product_name", "domain", "support_email", "branding", "categories", "policies", "entity_patterns"):
            if key in kwargs:
                config_overrides.setdefault(key, kwargs.pop(key))

        effective_config: Any = config
        if config_overrides:
            if config is None:
                effective_config = dict(config_overrides)
            elif isinstance(config, dict):
                effective_config = {**config, **config_overrides}
            elif isinstance(config, SaaSConfig):
                effective_config = {**config.to_dict(), **config_overrides}
            else:
                # Path / JSON string / YAML string: load it, then override.
                effective_config = {**load_saas_config(config).to_dict(), **config_overrides}

        super().__init__(
            config=effective_config,
            adapter=adapter,
            tool_registry=tool_registry,
            driver=driver,
            **kwargs,
        )

    def resolve(
        self,
        complaint: str,
        customer_id: Optional[str] = None,
        order_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        auto_execute: bool = True,
        dry_run: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Resolve a complaint synchronously and return a result dict."""
        return self.resolve_ticket_sync(
            complaint,
            customer_id=customer_id,
            order_id=order_id,
            context=context,
            auto_execute=auto_execute,
            dry_run=dry_run,
            **kwargs,
        )

    async def resolve_async(
        self,
        complaint: str,
        customer_id: Optional[str] = None,
        order_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        auto_execute: bool = True,
        dry_run: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Resolve a complaint asynchronously and return a result dict."""
        return await self.resolve_ticket(
            complaint,
            customer_id=customer_id,
            order_id=order_id,
            context=context,
            auto_execute=auto_execute,
            dry_run=dry_run,
            **kwargs,
        )

    __call__ = resolve


# Backwards-friendly alias: Troy is the public name, TroyClient the SDK name.
TroyClient = Troy  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Module-level convenience API (one shared default agent, created lazily)
# ---------------------------------------------------------------------------

_default_agent: Optional[Troy] = None


def get_default_agent() -> Troy:
    """Return the shared default :class:`Troy` agent (created lazily)."""
    global _default_agent
    if _default_agent is None:
        _default_agent = Troy()
    return _default_agent


# Convenience handle mirroring the SDK's default-client pattern.
def default_agent() -> Troy:  # noqa: F811 - function, not a variable
    return get_default_agent()


def resolve(
    complaint: str,
    customer_id: Optional[str] = None,
    order_id: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    auto_execute: bool = True,
    dry_run: bool = False,
    agent: Optional[Troy] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Resolve ``complaint`` with the shared default agent (or ``agent``).

    >>> result = troy.resolve("How do I export my data?")
    >>> assert result["ok"]
    """
    return (agent or get_default_agent()).resolve(
        complaint,
        customer_id=customer_id,
        order_id=order_id,
        context=context,
        auto_execute=auto_execute,
        dry_run=dry_run,
        **kwargs,
    )


async def resolve_async(
    complaint: str,
    customer_id: Optional[str] = None,
    order_id: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    auto_execute: bool = True,
    dry_run: bool = False,
    agent: Optional[Troy] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Async form of :func:`resolve`."""
    return await (agent or get_default_agent()).resolve_async(
        complaint,
        customer_id=customer_id,
        order_id=order_id,
        context=context,
        auto_execute=auto_execute,
        dry_run=dry_run,
        **kwargs,
    )


def tool(*args: Any, **kwargs: Any) -> Any:
    """
    Decorator that registers a tool on the shared default agent::

        @troy.tool(name="reset_quota", description="Reset a quota")
        def reset_quota(customer_id: str = "", **kwargs):
            return {"ok": True}

    Also usable bare (``@troy.tool``) or with a positional name
    (``@troy.tool("reset_quota")``). Per-instance tools should use
    ``@agent.tool`` instead.
    """
    return get_default_agent().tool(*args, **kwargs)


def register_tool(*args: Any, **kwargs: Any) -> Any:
    """Register a tool on the shared default agent (see :func:`tool`)."""
    return get_default_agent().register_tool(*args, **kwargs)


def execute_tool(tool_name: str, params: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
    """Execute a tool registered on the shared default agent."""
    return get_default_agent().execute_tool(tool_name, params, **kwargs)


# ---------------------------------------------------------------------------
# Serving (optional FastAPI surface, for users who want HTTP)
# ---------------------------------------------------------------------------


def serve(port: int = 3000, host: str = "0.0.0.0") -> None:
    """Serve Troy's FastAPI app on ``host:port`` (blocking)."""
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - uvicorn is a project dep
        raise RuntimeError(
            "Serving Troy requires uvicorn (install project requirements: pip install -r requirements.txt)"
        ) from exc
    uvicorn.run("app.main:app", host=host, port=int(port))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="troy.py",
        description="Troy — open-source complaint resolution. "
        "Resolve a customer complaint from the command line, or serve Troy over HTTP.",
        epilog="Examples:\n"
        "  python troy.py \"I was charged twice for order ORD-88219\"\n"
        "  python troy.py \"How do I export my data?\" --json\n"
        "  python troy.py \"I was charged twice\" --dry-run --json\n"
        "  python troy.py --serve --port 3000\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "complaint",
        nargs="?",
        help="The customer complaint to resolve (omit with --serve/--version/--help).",
    )
    parser.add_argument(
        "--auto-execute",
        dest="auto_execute",
        action="store_true",
        default=True,
        help="Execute the recommended remediation automatically (default: on).",
    )
    parser.add_argument(
        "--no-auto-execute",
        dest="auto_execute",
        action="store_false",
        help="Only recommend an action; do not execute it.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Preview the resolution without mutating any data.",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        default=False,
        help="Print the full resolution as JSON.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to a JSON/YAML config file, or a JSON string.",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        default=False,
        help="Serve Troy's FastAPI app instead of resolving a complaint.",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind when using --serve (default: 0.0.0.0).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=3000,
        help="Port to bind when using --serve (default: 3000).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"troy {__version__}",
        help="Show Troy's version and exit.",
    )
    return parser


def main(argv: Optional[list] = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.serve:
        serve(port=args.port, host=args.host)
        return 0

    if not args.complaint or not str(args.complaint).strip():
        parser.error("a complaint is required (or use --serve / --version / --help)")

    agent = Troy(config=args.config) if args.config else Troy()
    result = agent.resolve(
        str(args.complaint),
        auto_execute=bool(args.auto_execute),
        dry_run=bool(args.dry_run),
    )

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        status = result.get("status", "")
        category = result.get("category", "")
        communication = result.get("customer_communication") or result.get("communication") or ""
        print(f"Status: {status}")
        if category:
            print(f"Category: {category}")
        if result.get("customer_id"):
            print(f"Customer: {result['customer_id']}")
        if result.get("order_id"):
            print(f"Order: {result['order_id']}")
        if communication:
            print(f"\n{communication}")
        summary = result.get("summary")
        if summary:
            print(f"\nSummary: {summary}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
