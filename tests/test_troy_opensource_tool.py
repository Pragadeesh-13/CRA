"""
Tests for Troy as a clean, plug-and-play, open-source tool.

Troy is intentionally **not** tenant-based: ``import troy`` gives you a
single agent with a one-call ``resolve()``, a ``Troy`` class you can
instantiate and extend with ``@agent.tool``, three data adapters, a
``load_config`` helper, and a CLI (``python troy.py ...``).

Covers:
  - Canonical exports: Troy, resolve, tool, load_config,
    CsvDataAdapter, InMemoryDataAdapter, RestApiDataAdapter.
  - Direct usage: ``troy.resolve("...")``, ``agent = troy.Troy()``,
    ``@agent.tool`` decorator, module-level ``@troy.tool``.
  - CLI: ``--help``, ``--version``, resolving a complaint
    (plain / ``--json`` / ``--dry-run`` / ``--auto-execute``), and
    ``--serve`` argument handling (mocked, so it never blocks).
"""

import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

import troy

REPO_ROOT = Path(__file__).resolve().parent.parent
TROY_PY = REPO_ROOT / "troy.py"


def _seeded_adapter():
    """InMemory store with a duplicate-charge customer (Alex Morgan)."""
    return troy.InMemoryDataAdapter(
        customers=[
            {
                "customer_id": "USR-10492",
                "customer_name": "Alex Morgan",
                "customer_email": "alex.morgan@gmail.com",
                "reference_order_id": "ORD-88219",
            }
        ],
        transactions=[
            {
                "transaction_id": "TXN-9011",
                "customer_id": "USR-10492",
                "customer_name": "Alex Morgan",
                "customer_email": "alex.morgan@gmail.com",
                "order_id": "ORD-88219",
                "transaction_date": "2026-10-20 08:14:22",
                "amount": "49.00",
                "status": "Settled",
            },
            {
                "transaction_id": "TXN-9012",
                "customer_id": "USR-10492",
                "customer_name": "Alex Morgan",
                "customer_email": "alex.morgan@gmail.com",
                "order_id": "ORD-88219",
                "transaction_date": "2026-10-21 09:30:15",
                "amount": "49.00",
                "status": "Settled",
            },
        ],
    )


DUPLICATE = "I was charged twice for order ORD-88219, customer USR-10492, transaction TXN-9012"


# ---------------------------------------------------------------------------
# Canonical exports / import surface
# ---------------------------------------------------------------------------


class TestCanonicalExports:
    def test_troy_py_exists_at_repo_root(self):
        assert TROY_PY.exists()

    def test_canonical_exports_present(self):
        for name in (
            "Troy",
            "resolve",
            "tool",
            "load_config",
            "CsvDataAdapter",
            "InMemoryDataAdapter",
            "RestApiDataAdapter",
        ):
            assert hasattr(troy, name), f"troy.{name} is missing"
            assert name in troy.__all__

    def test_version_exposed(self):
        assert troy.__version__
        assert isinstance(troy.__version__, str)

    def test_adapters_are_the_app_adapters(self):
        from app.core.adapter import CsvDataAdapter, InMemoryDataAdapter, RestApiDataAdapter

        assert troy.CsvDataAdapter is CsvDataAdapter
        assert troy.InMemoryDataAdapter is InMemoryDataAdapter
        assert troy.RestApiDataAdapter is RestApiDataAdapter

    def test_load_config_defaults_and_dict(self):
        default = troy.load_config()
        assert default.product_name
        custom = troy.load_config({"product_name": "OpenSourceCo", "domain": "oss.example"})
        assert custom.product_name == "OpenSourceCo"
        assert custom.domain == "oss.example"

    def test_load_config_from_json_string(self):
        config = troy.load_config('{"product_name": "JsonCo"}')
        assert config.product_name == "JsonCo"


# ---------------------------------------------------------------------------
# Direct usage: troy.resolve / troy.Troy / @tool
# ---------------------------------------------------------------------------


class TestDirectUsage:
    def test_module_level_resolve_inquiry(self):
        result = troy.resolve("How do I export my data?")
        assert result["ok"] is True
        assert result["customer_communication"]
        json.dumps(result)  # JSON-serialisable

    def test_module_level_resolve_requires_no_tenant(self):
        # The whole point of the open-source tool: no tenant_id anywhere.
        result = troy.resolve("How do I reset my password?")
        assert result["ok"] is True

    def test_troy_instance_resolve(self):
        agent = troy.Troy()
        result = agent.resolve("How do I export my data?")
        assert result["ok"] is True
        assert result["customer_communication"]

    def test_troy_instance_with_config_and_adapter(self):
        agent = troy.Troy(
            config={"product_name": "MyShop", "domain": "myshop.example"},
            adapter=_seeded_adapter(),
        )
        assert agent.config.product_name == "MyShop"
        result = agent.resolve(DUPLICATE, auto_execute=True)
        assert result["ok"] is True
        assert result["product_name"] == "MyShop"
        assert result["status"] == "resolved"
        assert result["refund_id"]
        assert "Alex" in result["customer_communication"]

    def test_troy_instance_resolve_ticket_sync_and_async_aliases(self):
        agent = troy.Troy(adapter=_seeded_adapter())
        sync_result = agent.resolve_ticket_sync("How do I export my data?")
        assert sync_result["ok"] is True
        assert hasattr(agent, "resolve_async")
        assert hasattr(agent, "resolve_ticket")

    @pytest.mark.asyncio
    async def test_troy_async_resolve(self):
        agent = troy.Troy()
        result = await agent.resolve_async("How do I export my data?")
        assert result["ok"] is True
        result2 = await agent.resolve_ticket("How do I export my data?")
        assert result2["ok"] is True

    def test_troy_dry_run_does_not_mutate(self):
        adapter = _seeded_adapter()
        agent = troy.Troy(adapter=adapter)
        result = agent.resolve(DUPLICATE, dry_run=True)
        assert result["ok"] is True
        assert result["dry_run"] is True
        assert all(t["status"] != "Refunded" for t in adapter.get_transactions("USR-10492"))

    def test_agent_tool_decorator(self):
        agent = troy.Troy()
        tool_name = f"reset_quota_{uuid.uuid4().hex[:6]}"

        @agent.tool(name=tool_name, description="Reset a customer's quota")
        def reset_quota(customer_id: str = "", **kwargs):
            return {"ok": True, "customer_id": customer_id, "quota": "reset"}

        assert tool_name in agent.tool_registry.tool_names()
        listed = agent.list_tools()
        assert any(t["name"] == tool_name for t in listed)
        executed = agent.execute_tool(tool_name, {"customer_id": "USR-10492"})
        assert executed["ok"] is True
        assert executed["quota"] == "reset"
        assert executed["customer_id"] == "USR-10492"

    def test_agent_tool_bare_decorator(self):
        agent = troy.Troy()

        @agent.tool
        def ping_tool(**kwargs):
            return {"ok": True, "pong": True}

        assert "ping_tool" in agent.tool_registry.tool_names()
        assert agent.execute_tool("ping_tool", {})["pong"] is True

    def test_module_level_tool_decorator(self):
        tool_name = f"module_tool_{uuid.uuid4().hex[:6]}"

        @troy.tool(name=tool_name, description="Module-level tool")
        def module_tool(message: str = "hi", **kwargs):
            return {"ok": True, "message": message}

        agent = troy.get_default_agent()
        assert tool_name in agent.tool_registry.tool_names()
        executed = troy.execute_tool(tool_name, {"message": "hello"})
        assert executed["ok"] is True
        assert executed["message"] == "hello"

    def test_adapters_constructible_directly(self):
        memory = troy.InMemoryDataAdapter(customers=[{"customer_id": "USR-1", "customer_name": "A"}])
        assert memory.get_customer("USR-1")["customer_name"] == "A"
        csv_adapter = troy.CsvDataAdapter()
        assert csv_adapter is not None
        rest = troy.RestApiDataAdapter("https://example.com")
        assert rest.base_url == "https://example.com"


# ---------------------------------------------------------------------------
# CLI (subprocess + in-process main)
# ---------------------------------------------------------------------------


def _run_cli(*args, timeout=60):
    return subprocess.run(
        [sys.executable, str(TROY_PY), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestCliSubprocess:
    def test_cli_help(self):
        proc = _run_cli("--help")
        assert proc.returncode == 0
        out = (proc.stdout + proc.stderr).lower()
        assert "usage" in out
        assert "complaint" in out
        assert "--json" in out
        assert "--serve" in out
        assert "--dry-run" in out
        assert "--auto-execute" in out

    def test_cli_version(self):
        proc = _run_cli("--version")
        assert proc.returncode == 0
        out = (proc.stdout + proc.stderr).strip().lower()
        assert "troy" in out
        assert troy.__version__.lower() in out

    def test_cli_resolve_plain(self):
        proc = _run_cli("How do I export my data?")
        assert proc.returncode == 0
        assert "resolved" in proc.stdout.lower()
        assert "export" in proc.stdout.lower()

    def test_cli_resolve_json(self):
        proc = _run_cli("How do I export my data?", "--json")
        assert proc.returncode == 0
        data = json.loads(proc.stdout)
        assert data["ok"] is True
        assert data["customer_communication"]

    def test_cli_resolve_auto_execute_and_dry_run_flags(self):
        proc = _run_cli("I was charged twice for order ORD-88219", "--auto-execute", "--json")
        assert proc.returncode == 0
        data = json.loads(proc.stdout)
        assert data["ok"] is True
        assert data["auto_execute"] is True

        dry = _run_cli("I was charged twice", "--dry-run", "--json")
        assert dry.returncode == 0
        dry_data = json.loads(dry.stdout)
        assert dry_data["dry_run"] is True

    def test_cli_no_complaint_is_an_error(self):
        proc = _run_cli()
        assert proc.returncode != 0
        assert "complaint" in (proc.stdout + proc.stderr).lower()


class TestCliInProcess:
    def test_main_json(self, capsys):
        code = troy.main(["How do I export my data?", "--json"])
        assert code == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True

    def test_main_plain(self, capsys):
        code = troy.main(["How do I export my data?"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Status:" in out
        assert "resolved" in out.lower()

    def test_main_dry_run_flag(self, capsys):
        code = troy.main(["I was charged twice", "--dry-run", "--json"])
        assert code == 0
        assert json.loads(capsys.readouterr().out)["dry_run"] is True

    def test_main_serve_delegates_to_serve(self, monkeypatch):
        calls = {}

        def fake_serve(port=3000, host="0.0.0.0"):
            calls["port"] = port
            calls["host"] = host

        monkeypatch.setattr(troy, "serve", fake_serve)
        assert troy.main(["--serve"]) == 0
        assert calls == {"port": 3000, "host": "0.0.0.0"}

        calls.clear()
        assert troy.main(["--serve", "--port", "8000"]) == 0
        assert calls["port"] == 8000

    def test_parser_defaults(self):
        parser = troy._build_parser()
        args = parser.parse_args(["some complaint"])
        assert args.complaint == "some complaint"
        assert args.port == 3000
        assert args.dry_run is False
        assert args.json_output is False
        assert args.serve is False
