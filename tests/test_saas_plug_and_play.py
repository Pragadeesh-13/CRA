"""
Comprehensive test suite for Troy's SaaS plug-and-play layer (Phases 1-2).

Covers:
  - SaaSConfig: JSON/YAML (de)serialisation, file round-trips, custom
    entity extractors, custom categories/SLAs, approval policy logic.
  - Adapters: InMemoryDataAdapter, CsvDataAdapter (isolated_database
    fixture), RestApiDataAdapter (mock HTTP transport).
  - ToolRegistry: decorator + programmatic registration, schema
    validation, idempotency replay, dry-run, audit trail.
  - TroyClient (SDK): inquiries, autonomous duplicate-charge
    remediation, idempotent re-resolution, custom tenant config,
    dry-run, resolve_ticket_sync.
  - SaaS API & webhooks via FastAPI TestClient, and the embed widget.
"""

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.v1.saas_routes import SUPPORTED_PROVIDERS, TenantRegistry, normalize_webhook_payload, tenant_registry
from app.core.adapter import CsvDataAdapter, InMemoryDataAdapter, RestApiDataAdapter
from app.core.saas_config import SaaSConfig, load_saas_config
from app.core.tool_registry import TroyToolRegistry
from app.main import app
from app.sdk import TroyClient, create_troy_router


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def seeded_adapter() -> InMemoryDataAdapter:
    """InMemory store with a duplicate-charge customer (Alex Morgan)."""
    return InMemoryDataAdapter(
        customers=[
            {
                "customer_id": "USR-10492",
                "customer_name": "Alex Morgan",
                "customer_email": "alex.morgan@gmail.com",
                "reference_order_id": "ORD-88219",
            },
            {
                "customer_id": "USR-07231",
                "customer_name": "Emily Watson",
                "customer_email": "emily.w@consulting.co",
                "reference_order_id": "ORD-81094",
            },
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


DUPLICATE_COMPLAINT = "I was charged twice for order ORD-88219, customer USR-10492, transaction TXN-9012"


# ---------------------------------------------------------------------------
# SaaSConfig
# ---------------------------------------------------------------------------


class TestSaaSConfig:
    def test_defaults(self):
        config = SaaSConfig()
        assert config.product_name == "Troy Demo SaaS"
        assert config.support_email
        assert config.domain
        assert config.entity_patterns
        assert config.categories
        assert config.policies.auto_refund_limit is not None

    def test_json_serialization_round_trip(self):
        config = SaaSConfig(product_name="CloudScribe", domain="cloudscribe.io")
        payload = config.to_json()
        assert json.loads(payload)["product_name"] == "CloudScribe"

        restored = SaaSConfig.from_json(payload)
        assert restored.product_name == "CloudScribe"
        assert restored.domain == "cloudscribe.io"
        assert restored.to_dict()["product_name"] == "CloudScribe"

    def test_yaml_serialization_round_trip(self):
        config = SaaSConfig(product_name="DevMetrics", domain="devmetrics.example")
        yaml_text = config.to_yaml()
        assert "DevMetrics" in yaml_text
        restored = SaaSConfig.from_yaml(yaml_text)
        assert restored.product_name == "DevMetrics"
        assert restored.domain == "devmetrics.example"

    def test_json_file_round_trip(self, tmp_path):
        path = tmp_path / "saas.json"
        SaaSConfig(product_name="JsonSaaS").save(path)
        loaded = load_saas_config(path)
        assert loaded.product_name == "JsonSaaS"
        # Also loads from a string path.
        assert load_saas_config(str(path)).product_name == "JsonSaaS"

    def test_yaml_file_round_trip(self, tmp_path):
        path = tmp_path / "saas.yaml"
        SaaSConfig(product_name="YamlSaaS", policies={"auto_refund_limit": 25, "dry_run": True}).save(path)
        loaded = load_saas_config(path)
        assert loaded.product_name == "YamlSaaS"
        assert loaded.policies.auto_refund_limit == 25
        assert loaded.policies.dry_run is True

    def test_load_from_dict_and_string_payload(self):
        assert load_saas_config({"product_name": "DictSaaS"}).product_name == "DictSaaS"
        assert load_saas_config('{"product_name": "StrSaaS"}').product_name == "StrSaaS"
        assert load_saas_config().product_name == "Troy Demo SaaS"
        assert load_saas_config(SaaSConfig(product_name="Kept")).product_name == "Kept"

    def test_custom_entity_extractors(self):
        config = SaaSConfig(
            product_name="CloudScribe",
            entity_patterns={
                "charges": [r"ch_\d+"],
                "invoices": [r"INV-\d+"],
                "tenants": [r"org_\d+"],
            },
        )
        found = config.extract_entities("Stripe charge ch_123, invoice INV-99, tenant org_88")
        assert found["charges"] == ["ch_123"]
        assert found["invoices"] == ["INV-99"]
        assert found["tenants"] == ["org_88"]

    def test_default_entity_extractors(self):
        found = SaaSConfig().extract_entities("Order ORD-88219 for USR-10492, TXN-9012")
        assert "ORD-88219" in found["orders"]
        assert "USR-10492" in found["customers"]
        assert "TXN-9012" in found["transactions"]

    def test_invalid_entity_pattern_rejected(self):
        with pytest.raises(Exception):
            SaaSConfig(entity_patterns={"orders": ["([invalid"]})

    def test_custom_categories_and_slas(self):
        config = SaaSConfig(
            categories=[
                {"name": "Refunds", "sla_hours": 2.0, "sla_risk": "Critical", "team": "Billing"},
                "API Quota",
            ]
        )
        assert config.category_names() == ["Refunds", "API Quota"]
        refunds = config.get_category("Refunds")
        assert refunds is not None
        assert refunds.sla_hours == 2.0
        sla = config.get_sla("Refunds")
        assert sla["estimated_hours"] == 2.0
        assert sla["sla_risk"] == "Critical"
        assert config.get_category("API Quota").sla_hours == 8.0  # default SLA
        assert config.get_category("Does Not Exist") is None

    def test_approval_policy_logic(self):
        config = SaaSConfig(policies={"auto_refund_limit": 10.0, "approval_required": True})
        assert config.policies.auto_refund_limit == 10.0
        assert config.requires_approval_for(20.0) is True
        assert config.requires_approval_for(5.0) is False
        assert config.policies.requires_approval_for(20.0) is True

    def test_branding_and_aliases(self):
        config = SaaSConfig(name="AliasSaaS", email="help@alias.example", branding="AliasSaaS")
        assert config.product_name == "AliasSaaS"
        assert config.support_email == "help@alias.example"
        assert config.branding.name == "AliasSaaS"


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


class TestInMemoryAdapter:
    def test_seeding_and_customer_queries(self):
        adapter = seeded_adapter()
        by_id = adapter.get_customer("USR-10492")
        assert by_id["customer_name"] == "Alex Morgan"
        by_email = adapter.get_customer("alex.morgan@gmail.com")
        assert by_email["customer_id"] == "USR-10492"
        by_order = adapter.get_customer("ORD-88219")
        assert by_order["customer_id"] == "USR-10492"
        assert adapter.get_customer("USR-99999") is None
        assert adapter.get_customer("") is None

    def test_seeding_from_dict_form(self):
        adapter = InMemoryDataAdapter(
            {
                "customers": {"USR-1": {"customer_name": "Dict User"}},
                "transactions": [{"transaction_id": "TXN-1", "customer_id": "USR-1", "status": "Settled"}],
            }
        )
        assert adapter.get_customer("USR-1")["customer_name"] == "Dict User"
        assert len(adapter.get_transactions("USR-1")) == 1

    def test_transaction_queries_and_filters(self):
        adapter = seeded_adapter()
        txns = adapter.get_transactions("USR-10492")
        assert {t["transaction_id"] for t in txns} == {"TXN-9011", "TXN-9012"}
        assert adapter.get_transactions("USR-07231") == []
        settled = adapter.get_transactions("USR-10492", status="Settled")
        assert len(settled) == 2

    def test_action_execution_and_idempotent_refund(self):
        adapter = seeded_adapter()
        result = adapter.execute_action(
            "remediate_duplicate_charge",
            {"order_id": "ORD-88219", "duplicate_txn_id": "TXN-9012", "refund_amount": "49.00"},
        )
        assert result["ok"] is True
        assert result["status"] == "Refunded"
        txn = next(t for t in adapter.get_transactions("USR-10492") if t["transaction_id"] == "TXN-9012")
        assert txn["status"] == "Refunded"

        again = adapter.execute_action("remediate_duplicate_charge", {"duplicate_txn_id": "TXN-9012"})
        assert again["ok"] is True
        assert again["is_already_refunded"] is True

        missing = adapter.execute_action("remediate_duplicate_charge", {"duplicate_txn_id": "TXN-0000"})
        assert missing["ok"] is False

        unknown = adapter.execute_action("no_such_action", {})
        assert unknown["ok"] is False

    def test_update_customer_account_action(self):
        adapter = seeded_adapter()
        result = adapter.execute_action(
            "update_customer_account",
            {"customer_id": "USR-10492", "updates": {"account_status": "Active"}},
        )
        assert result["ok"] is True
        assert adapter.get_customer("USR-10492")["account_status"] == "Active"

    def test_audit_log(self):
        adapter = seeded_adapter()
        adapter.get_customer("USR-10492")
        adapter.audit_log({"event": "manual_entry", "detail": "hello"})
        trail = adapter.audit_trail
        assert trail
        assert any(entry.get("event") == "manual_entry" for entry in trail)


class TestCsvAdapter:
    def test_customer_and_transaction_queries(self, isolated_database):
        adapter = CsvDataAdapter()
        customer = adapter.get_customer("USR-10492")
        assert customer is not None
        assert customer["customer_name"] == "Alex Morgan"
        assert customer["reference_order_id"] == "ORD-88219"

        txns = adapter.get_transactions("USR-10492")
        assert len(txns) == 2
        assert adapter.get_customer("USR-99999") is None

    def test_refund_action_persists_to_isolated_csv(self, isolated_database):
        import app.database as database

        adapter = CsvDataAdapter()
        result = adapter.execute_action(
            "remediate_duplicate_charge",
            {"order_id": "ORD-81094", "duplicate_txn_id": "TXN-7712", "refund_amount": "49.00"},
        )
        assert result["ok"] is True
        assert result["status"] == "Refunded"
        txns = database.read_all_transactions()
        txn = next(t for t in txns if t["transaction_id"] == "TXN-7712")
        assert txn["status"] == "Refunded"
        # Idempotent replay does not double-refund.
        again = adapter.execute_action("remediate_duplicate_charge", {"duplicate_txn_id": "TXN-7712"})
        assert again["ok"] is True

    def test_audit_log(self, isolated_database):
        adapter = CsvDataAdapter()
        adapter.get_customer("USR-10492")
        adapter.audit_log({"event": "csv_manual", "ok": True})
        assert any(entry.get("event") == "csv_manual" for entry in adapter.audit_trail)


class _FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeHttpClient:
    """Mock HTTP transport recording calls and serving canned SaaS data."""

    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if "/customers/" in url:
            identifier = url.rstrip("/").split("/")[-1]
            return _FakeResponse({"customer_id": identifier, "customer_name": "Remote User"})
        if "/transactions" in url:
            return _FakeResponse(
                {
                    "transactions": [
                        {"transaction_id": "TXN-9011", "customer_id": "USR-10492", "amount": "49.00", "status": "Settled"},
                        {"transaction_id": "TXN-9012", "customer_id": "USR-10492", "amount": "49.00", "status": "Settled"},
                    ]
                }
            )
        return _FakeResponse({})

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if "/actions/" in url:
            return _FakeResponse({"ok": True, "status": "Refunded", "action": url.rstrip("/").split("/")[-1]})
        return _FakeResponse({"ok": True})


class TestRestApiAdapter:
    def test_customer_lookup_via_mock_transport(self):
        fake = _FakeHttpClient()
        adapter = RestApiDataAdapter("https://saas.example.com", http_client=fake)
        customer = adapter.get_customer("USR-10492")
        assert customer["customer_id"] == "USR-10492"
        assert customer["customer_name"] == "Remote User"
        assert any(method == "GET" and "/customers/USR-10492" in url for method, url, _ in fake.calls)

    def test_transactions_query_via_mock_transport(self):
        fake = _FakeHttpClient()
        adapter = RestApiDataAdapter("https://saas.example.com", http_client=fake)
        txns = adapter.get_transactions("USR-10492")
        assert len(txns) == 2
        assert txns[0]["transaction_id"] == "TXN-9011"

    def test_action_dispatch_via_mock_transport(self):
        fake = _FakeHttpClient()
        adapter = RestApiDataAdapter("https://saas.example.com", http_client=fake)
        result = adapter.execute_action("issue_refund", {"transaction_id": "TXN-9012"})
        assert result["ok"] is True
        assert result["status"] == "Refunded"
        assert any(method == "POST" and "/actions/issue_refund" in url for method, url, _ in fake.calls)

    def test_audit_log_posts_to_remote_endpoint(self):
        fake = _FakeHttpClient()
        adapter = RestApiDataAdapter("https://saas.example.com", http_client=fake)
        adapter.audit_log({"event": "remote_audit", "ok": True})
        assert any(method == "POST" and url.endswith("/audit") for method, url, _ in fake.calls)


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------


class TestToolRegistry:
    def test_decorator_registration(self):
        registry = TroyToolRegistry()

        @registry.tool(name="greet", description="Greet a customer", schema={"type": "object", "properties": {"name": {"type": "string"}}})
        def greet(name: str = "world"):
            return {"ok": True, "message": f"Hello {name}"}

        assert "greet" in registry.tool_names()
        result = registry.execute("greet", {"name": "Alex"})
        assert result["ok"] is True
        # Handlers returning a mapping have it merged into the result dict;
        # non-mapping returns are nested under "result".
        assert result.get("result") == "Hello Alex" or result.get("message") == "Hello Alex"

    def test_programmatic_registration_both_orders(self):
        registry = TroyToolRegistry()
        registry.register_tool(lambda q="hi": {"ok": True, "q": q}, name="echo")
        registry.register_tool("shout", lambda word="hi": {"ok": True, "word": word.upper()})

        assert registry.execute("echo", {"q": "hey"})["q"] == "hey"
        assert registry.execute("shout", {"word": "hey"})["word"] == "HEY"

    def test_schema_and_parameter_validation(self):
        registry = TroyToolRegistry()
        registry.register_tool(
            lambda transaction_id, amount=0: {"ok": True},
            name="refund",
            schema={
                "type": "object",
                "required": ["transaction_id"],
                "properties": {"transaction_id": {"type": "string"}, "amount": {"type": "number"}},
            },
        )
        missing = registry.execute("refund", {})
        assert missing["ok"] is False
        assert "transaction_id" in missing["error"]

        wrong_type = registry.execute("refund", {"transaction_id": "TXN-1", "amount": "lots"})
        assert wrong_type["ok"] is False
        assert "amount" in wrong_type["error"]

        valid = registry.execute("refund", {"transaction_id": "TXN-1", "amount": 49.0})
        assert valid["ok"] is True

    def test_unknown_tool_fails_gracefully(self):
        registry = TroyToolRegistry()
        result = registry.execute("nope", {})
        assert result["ok"] is False
        assert "Unknown tool" in result["error"]

    def test_idempotency_key_replay_does_not_reexecute(self):
        registry = TroyToolRegistry()
        calls = {"count": 0}

        def charge():
            calls["count"] += 1
            return {"ok": True, "call": calls["count"]}

        registry.register_tool(charge, name="charge")
        first = registry.execute("charge", {}, idempotency_key="req-42")
        replay = registry.execute("charge", {}, idempotency_key="req-42")

        assert first["ok"] is True
        assert replay.get("idempotent_replay") is True
        assert calls["count"] == 1  # handler ran exactly once

    def test_dry_run_mode(self):
        registry = TroyToolRegistry()
        calls = {"count": 0}

        def charge():
            calls["count"] += 1
            return {"ok": True}

        registry.register_tool(charge, name="charge")
        dry = registry.execute("charge", {}, dry_run=True)
        assert dry["ok"] is True
        assert dry["dry_run"] is True
        assert dry["status"] == "dry_run"
        assert calls["count"] == 0  # handler never invoked

    def test_audit_trail_tracking(self):
        registry = TroyToolRegistry()
        registry.register_tool(lambda: {"ok": True}, name="ping")
        registry.execute("ping", {})
        registry.execute("ping", {}, dry_run=True)
        registry.execute("missing_tool", {})
        trail = registry.get_audit_trail()
        assert len(trail) == 3
        assert trail[0]["tool"] == "ping"
        assert any(entry.get("tool") == "missing_tool" for entry in trail)


# ---------------------------------------------------------------------------
# TroyClient (SDK)
# ---------------------------------------------------------------------------


class TestTroyClient:
    def test_defaults(self):
        client = TroyClient()
        assert client.config.product_name == "Troy Demo SaaS"
        assert isinstance(client.adapter, InMemoryDataAdapter)
        assert type(client.driver).__name__ == "MockDriver"
        assert "issue_refund" in client.tool_registry.tool_names()
        assert "update_customer_account" in client.tool_registry.tool_names()

    def test_custom_tenant_configuration_cloudscribe(self):
        client = TroyClient(config={"product_name": "CloudScribe", "domain": "cloudscribe.io"})
        assert client.config.product_name == "CloudScribe"
        result = client.resolve_ticket_sync("How do I invite my team?")
        assert result["ok"] is True
        assert result["product_name"] == "CloudScribe"

    @pytest.mark.asyncio
    async def test_inquiry_direct_response(self):
        client = TroyClient(adapter=seeded_adapter())
        result = await client.resolve_ticket("How do I reset my password?")
        assert result["ok"] is True
        assert result["intent"]["intent"] == "direct_response"
        assert result["customer_communication"]
        json.dumps(result)  # must be JSON-serialisable

    @pytest.mark.asyncio
    async def test_autonomous_duplicate_charge_remediation(self):
        adapter = seeded_adapter()
        client = TroyClient(adapter=adapter)
        result = await client.resolve_ticket(DUPLICATE_COMPLAINT, auto_execute=True)

        assert result["ok"] is True
        assert result["status"] == "resolved"
        assert result["category"] == "Refunds"
        assert result["customer_id"] == "USR-10492"
        assert result["order_id"] == "ORD-88219"
        assert result["refund_id"]
        assert result["action_result"]["ok"] is True
        assert "Alex" in result["customer_communication"]
        txn = next(t for t in adapter.get_transactions("USR-10492") if t["transaction_id"] == "TXN-9012")
        assert txn["status"] == "Refunded"
        assert adapter.audit_trail  # audit logged

    @pytest.mark.asyncio
    async def test_idempotent_reresolution_of_already_refunded(self):
        adapter = seeded_adapter()
        client = TroyClient(adapter=adapter)
        first = await client.resolve_ticket(DUPLICATE_COMPLAINT)
        assert first["refund_id"]

        second = await client.resolve_ticket(DUPLICATE_COMPLAINT)
        assert second["ok"] is True
        assert second["status"] == "already_refunded"
        # Still exactly one refunded transaction — no double payout.
        refunded = [t for t in adapter.get_transactions("USR-10492") if t["status"] == "Refunded"]
        assert len(refunded) == 1

    @pytest.mark.asyncio
    async def test_dry_run_mode(self):
        adapter = seeded_adapter()
        client = TroyClient(adapter=adapter)
        result = await client.resolve_ticket(DUPLICATE_COMPLAINT, dry_run=True)
        assert result["ok"] is True
        assert result["dry_run"] is True
        assert result["status"] == "dry_run"
        txns = adapter.get_transactions("USR-10492")
        assert all(t["status"] != "Refunded" for t in txns)  # nothing mutated

    @pytest.mark.asyncio
    async def test_auto_execute_false_does_not_mutate(self):
        adapter = seeded_adapter()
        client = TroyClient(adapter=adapter)
        result = await client.resolve_ticket(DUPLICATE_COMPLAINT, auto_execute=False)
        assert result["action_result"]["status"] == "not_executed"
        assert all(t["status"] != "Refunded" for t in adapter.get_transactions("USR-10492"))

    def test_resolve_ticket_sync_wrapper(self):
        client = TroyClient(adapter=seeded_adapter())
        result = client.resolve_ticket_sync(DUPLICATE_COMPLAINT)
        assert result["ok"] is True
        assert result["status"] == "resolved"
        assert result["refund_id"]

    def test_register_tool_helper(self):
        client = TroyClient()

        @client.tool(name="reset_api_quota", description="Reset a tenant API quota")
        def reset_api_quota(customer_id: str = "", **kwargs):
            return {"ok": True, "customer_id": customer_id, "quota": "reset"}

        assert "reset_api_quota" in client.tool_registry.tool_names()
        result = client.execute_tool("reset_api_quota", {"customer_id": "USR-10492"})
        assert result["ok"] is True

        client.register_tool(lambda: {"ok": True}, name="ping_tool")
        assert client.execute_tool("ping_tool", {})["ok"] is True


# ---------------------------------------------------------------------------
# SaaS API & webhooks (TestClient against the real FastAPI app)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def api_client():
    return TestClient(app)


class TestSaaSApi:
    def test_info_endpoint(self, api_client):
        res = api_client.get("/api/v1/saas/info")
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["product_name"]
        capabilities = data["capabilities"]
        assert "zendesk" in capabilities["webhooks"]
        assert "stripe" in capabilities["webhooks"]
        assert capabilities["adapter_type"]

    def test_resolve_endpoint(self, api_client):
        res = api_client.post("/api/v1/saas/resolve", json={"complaint": "How do I export my data?"})
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["customer_communication"]

    def test_resolve_endpoint_requires_complaint(self, api_client):
        res = api_client.post("/api/v1/saas/resolve", json={})
        assert res.status_code == 400
        assert res.json()["ok"] is False

    def test_webhook_zendesk(self, api_client):
        res = api_client.post(
            "/api/v1/saas/webhooks/zendesk",
            json={
                "ticket": {
                    "subject": "Charged twice",
                    "description": "I was charged twice for ORD-88219 USR-10492 TXN-9012",
                    "requester": {"id": "USR-10492", "email": "alex.morgan@gmail.com", "name": "Alex Morgan"},
                }
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["provider"] == "zendesk"
        assert "charged twice" in data["complaint"].lower()
        assert data["normalized"]["customer_id"] == "USR-10492"

    def test_webhook_stripe(self, api_client):
        res = api_client.post(
            "/api/v1/saas/webhooks/stripe",
            json={
                "type": "charge.refunded",
                "data": {
                    "object": {
                        "id": "ch_123",
                        "customer": "cus_123",
                        "amount": 4900,
                        "currency": "usd",
                        "metadata": {"order_id": "ORD-88219", "customer_id": "USR-10492"},
                    }
                },
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["provider"] == "stripe"
        assert "stripe" in data["complaint"].lower()

    def test_webhook_intercom(self, api_client):
        res = api_client.post(
            "/api/v1/saas/webhooks/intercom",
            json={
                "type": "ticket.created",
                "data": {
                    "item": {"title": "Duplicate charge", "body": "I was charged twice for ORD-88219"},
                    "user": {"id": "USR-10492", "email": "alex.morgan@gmail.com", "name": "Alex Morgan"},
                },
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["provider"] == "intercom"
        assert "charged twice" in data["complaint"].lower()

    def test_webhook_freshdesk(self, api_client):
        res = api_client.post(
            "/api/v1/saas/webhooks/freshdesk",
            json={
                "ticket": {
                    "subject": "Refund please",
                    "description": "I was charged twice for ORD-88219",
                    "requester_id": "USR-10492",
                }
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["provider"] == "freshdesk"
        assert data["normalized"]["customer_id"] == "USR-10492"

    def test_webhook_generic(self, api_client):
        res = api_client.post(
            "/api/v1/saas/webhooks/generic",
            json={"complaint": "I was charged twice for ORD-88219 USR-10492", "customer_id": "USR-10492"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["provider"] == "generic"
        assert data["customer_id"] == "USR-10492"

    def test_sdk_middleware_router(self):
        from fastapi import FastAPI

        client = TroyClient(config={"product_name": "WidgetSaaS"}, adapter=seeded_adapter())
        mini_app = FastAPI()
        mini_app.include_router(create_troy_router(client))
        mini_client = TestClient(mini_app)

        health = mini_client.get("/health")
        assert health.status_code == 200
        assert health.json()["product_name"] == "WidgetSaaS"
        assert health.json()["adapter_type"] == "InMemoryDataAdapter"

        resolved = mini_client.post("/resolve", json={"complaint": DUPLICATE_COMPLAINT})
        assert resolved.status_code == 200
        assert resolved.json()["ok"] is True

        executed = mini_client.post("/tools/execute", json={"tool": "get_customer", "params": {"customer_id": "USR-10492"}})
        assert executed.status_code == 200
        assert executed.json()["customer"]["customer_name"] == "Alex Morgan"


class TestWidget:
    def test_widget_served_with_valid_js(self, api_client):
        res = api_client.get("/widget/troy-embed.js")
        assert res.status_code == 200
        body = res.text
        assert "TroyEmbed" in body
        assert "fetch(" in body
        assert "troy-drawer" in body
        assert "application/javascript" in res.headers.get("content-type", "") or "text/javascript" in res.headers.get("content-type", "")

    def test_widget_source_file_exists(self):
        repo_root = Path(__file__).resolve().parent.parent
        candidates = [
            repo_root / "app" / "static" / "widget" / "troy-embed.js",
            repo_root / "public" / "widget" / "troy-embed.js",
        ]
        assert any(path.exists() for path in candidates)


# ---------------------------------------------------------------------------
# Multi-tenant registry (plug-and-play SaaS, Phase 3)
# ---------------------------------------------------------------------------


def _unique_tenant_id(prefix: str = "tenant") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class TestTenantRegistry:
    def test_register_and_retrieve_client_by_tenant_id(self):
        registry = TenantRegistry()
        client = registry.register("acme-unit", config={"product_name": "Acme Unit", "domain": "acme.example"})
        assert client.config.product_name == "Acme Unit"
        assert registry.get("acme-unit") is client
        assert registry.get_client("acme-unit") is client
        assert registry.get_tenant("acme-unit") is client
        assert "acme-unit" in registry
        assert len(registry) == 1

    def test_register_dict_profile_and_lookup(self):
        registry = TenantRegistry()
        client = registry.register({"tenant_id": "dict-tenant", "config": {"product_name": "DictTenant"}})
        assert client.config.product_name == "DictTenant"
        assert registry.get("dict-tenant") is client

    def test_register_update_replaces_client(self):
        registry = TenantRegistry()
        first = registry.register("updatable", config={"product_name": "Before"})
        second = registry.register("updatable", config={"product_name": "After"})
        assert first is not second
        assert registry.get("updatable").config.product_name == "After"
        assert len(registry) == 1

    def test_list_tenants_returns_id_and_product_name(self):
        registry = TenantRegistry()
        registry.register("beta", config={"product_name": "BetaSaaS"})
        registry.register("alpha", config={"product_name": "AlphaSaaS"})
        listed = registry.list_tenants()
        assert {entry["tenant_id"] for entry in listed} == {"alpha", "beta"}
        by_id = {entry["tenant_id"]: entry["product_name"] for entry in listed}
        assert by_id["alpha"] == "AlphaSaaS"
        assert by_id["beta"] == "BetaSaaS"

    def test_resolve_falls_back_when_tenant_unknown(self):
        registry = TenantRegistry()
        fallback = TroyClient()
        assert registry.resolve("missing", fallback=fallback) is fallback
        assert registry.resolve("missing") is not None

    def test_unregister_and_clear(self):
        registry = TenantRegistry()
        registry.register("temporary", config={"product_name": "Temp"})
        assert registry.unregister("temporary") is True
        assert registry.get("temporary") is None
        registry.register("again", config={"product_name": "Again"})
        registry.clear()
        assert len(registry) == 0
        assert registry.list_tenants() == []

    def test_supported_providers_include_shopify_and_slack(self):
        assert "shopify" in SUPPORTED_PROVIDERS
        assert "slack" in SUPPORTED_PROVIDERS


class TestMultiTenantApi:
    def test_register_tenant_via_api(self, api_client):
        tenant_id = _unique_tenant_id("acme")
        res = api_client.post(
            "/api/v1/saas/tenants",
            json={"tenant_id": tenant_id, "config": {"product_name": "Acme XYZ", "domain": "acme.example"}},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["tenant_id"] == tenant_id
        assert data["product_name"] == "Acme XYZ"

    def test_register_tenant_requires_tenant_id(self, api_client):
        res = api_client.post("/api/v1/saas/tenants", json={"config": {"product_name": "NoId"}})
        assert res.status_code == 400
        assert res.json()["ok"] is False

    def test_register_tenant_with_top_level_config_fields(self, api_client):
        tenant_id = _unique_tenant_id("flat")
        res = api_client.post(
            "/api/v1/saas/tenants",
            json={"tenant_id": tenant_id, "product_name": "FlatSaaS", "domain": "flat.example"},
        )
        assert res.status_code == 200
        assert res.json()["product_name"] == "FlatSaaS"

    def test_register_tenant_update_via_api(self, api_client):
        tenant_id = _unique_tenant_id("update")
        first = api_client.post("/api/v1/saas/tenants", json={"tenant_id": tenant_id, "config": {"product_name": "BeforeUpdate"}})
        assert first.json()["product_name"] == "BeforeUpdate"
        second = api_client.post("/api/v1/saas/tenants", json={"tenant_id": tenant_id, "config": {"product_name": "AfterUpdate"}})
        assert second.status_code == 200
        assert second.json()["product_name"] == "AfterUpdate"
        fetched = api_client.get(f"/api/v1/saas/tenants/{tenant_id}")
        assert fetched.json()["product_name"] == "AfterUpdate"

    def test_header_based_tenant_resolution_on_info(self, api_client):
        tenant_id = _unique_tenant_id("header-info")
        api_client.post("/api/v1/saas/tenants", json={"tenant_id": tenant_id, "config": {"product_name": "HeaderInfoSaaS"}})
        res = api_client.get("/api/v1/saas/info", headers={"X-Tenant-ID": tenant_id})
        assert res.status_code == 200
        data = res.json()
        assert data["product_name"] == "HeaderInfoSaaS"
        assert data["tenant_id"] == tenant_id

    def test_header_based_tenant_resolution_on_resolve(self, api_client):
        tenant_id = _unique_tenant_id("header-resolve")
        api_client.post("/api/v1/saas/tenants", json={"tenant_id": tenant_id, "config": {"product_name": "HeaderResolveSaaS"}})
        res = api_client.post(
            "/api/v1/saas/resolve",
            json={"complaint": "How do I export my data?"},
            headers={"X-Tenant-ID": tenant_id},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["product_name"] == "HeaderResolveSaaS"
        assert data["tenant_id"] == tenant_id

    def test_query_param_tenant_resolution(self, api_client):
        tenant_id = _unique_tenant_id("query")
        api_client.post("/api/v1/saas/tenants", json={"tenant_id": tenant_id, "config": {"product_name": "QuerySaaS"}})
        info = api_client.get(f"/api/v1/saas/info?tenant={tenant_id}")
        assert info.status_code == 200
        assert info.json()["product_name"] == "QuerySaaS"
        resolved = api_client.post(f"/api/v1/saas/resolve?tenant={tenant_id}", json={"complaint": "How do I export my data?"})
        assert resolved.json()["product_name"] == "QuerySaaS"

    def test_payload_tenant_resolution(self, api_client):
        tenant_id = _unique_tenant_id("payload")
        api_client.post("/api/v1/saas/tenants", json={"tenant_id": tenant_id, "config": {"product_name": "PayloadSaaS"}})
        # tenant_id field
        res = api_client.post("/api/v1/saas/resolve", json={"complaint": "How do I export my data?", "tenant_id": tenant_id})
        assert res.json()["product_name"] == "PayloadSaaS"
        # tenantId camelCase alias
        res2 = api_client.post("/api/v1/saas/resolve", json={"complaint": "How do I export my data?", "tenantId": tenant_id})
        assert res2.json()["product_name"] == "PayloadSaaS"

    def test_unregistered_tenant_falls_back_to_default(self, api_client):
        res = api_client.get("/api/v1/saas/info", headers={"X-Tenant-ID": "no-such-tenant-xyz"})
        assert res.status_code == 200
        assert res.json()["ok"] is True

    def test_list_tenants_via_api(self, api_client):
        tenant_id = _unique_tenant_id("listable")
        api_client.post("/api/v1/saas/tenants", json={"tenant_id": tenant_id, "config": {"product_name": "ListableSaaS"}})
        res = api_client.get("/api/v1/saas/tenants")
        assert res.status_code == 200
        listed = res.json()
        assert isinstance(listed, list)
        entries = {entry["tenant_id"]: entry for entry in listed}
        assert tenant_id in entries
        assert entries[tenant_id]["product_name"] == "ListableSaaS"

    def test_get_tenant_info_and_capabilities(self, api_client):
        tenant_id = _unique_tenant_id("detail")
        api_client.post("/api/v1/saas/tenants", json={"tenant_id": tenant_id, "config": {"product_name": "DetailSaaS"}})
        res = api_client.get(f"/api/v1/saas/tenants/{tenant_id}")
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["tenant_id"] == tenant_id
        assert data["product_name"] == "DetailSaaS"
        capabilities = data["capabilities"]
        assert capabilities["resolve"] is True
        assert "shopify" in capabilities["webhooks"]
        assert "slack" in capabilities["webhooks"]
        assert "zendesk" in capabilities["webhooks"]

    def test_get_unknown_tenant_returns_404(self, api_client):
        res = api_client.get("/api/v1/saas/tenants/no-such-tenant-xyz")
        assert res.status_code == 404
        assert res.json()["ok"] is False

    def test_module_level_registry_reflects_api_registrations(self, api_client):
        tenant_id = _unique_tenant_id("shared")
        api_client.post("/api/v1/saas/tenants", json={"tenant_id": tenant_id, "config": {"product_name": "SharedRegistrySaaS"}})
        assert tenant_registry.get(tenant_id) is not None
        assert tenant_registry.get(tenant_id).config.product_name == "SharedRegistrySaaS"


# ---------------------------------------------------------------------------
# Shopify & Slack webhooks
# ---------------------------------------------------------------------------


class TestShopifySlackWebhooks:
    # -- direct normalisation -------------------------------------------

    def test_normalize_shopify_from_note_and_customer(self):
        normalized = normalize_webhook_payload(
            "shopify",
            {
                "note": "I was charged twice for ORD-88219",
                "name": "#1001",
                "order_number": 1001,
                "customer": {"id": "USR-10492", "email": "alex.morgan@gmail.com", "first_name": "Alex", "last_name": "Morgan"},
            },
        )
        assert normalized["provider"] == "shopify"
        assert "charged twice" in normalized["complaint"].lower()
        assert normalized["order_id"] == "#1001"
        assert normalized["customer_id"] == "USR-10492"
        assert normalized["customer_email"] == "alex.morgan@gmail.com"
        assert normalized["customer_name"] == "Alex Morgan"

    def test_normalize_shopify_from_cancel_reason(self):
        normalized = normalize_webhook_payload(
            "shopify",
            {"cancel_reason": "Customer cancelled after a duplicate charge", "order_number": "ORD-88219", "customer": {"id": "USR-10492"}},
        )
        assert "duplicate charge" in normalized["complaint"].lower()
        assert normalized["order_id"] == "ORD-88219"
        assert normalized["customer_id"] == "USR-10492"

    def test_normalize_shopify_from_reason_and_order_id(self):
        normalized = normalize_webhook_payload(
            "shopify",
            {"reason": "Item never arrived", "order_id": "ORD-81094", "customer": {"id": "USR-07231", "email": "emily.w@consulting.co"}},
        )
        assert "never arrived" in normalized["complaint"].lower()
        assert normalized["order_id"] == "ORD-81094"
        assert normalized["customer_email"] == "emily.w@consulting.co"

    def test_normalize_slack_from_text_and_user(self):
        normalized = normalize_webhook_payload(
            "slack",
            {"text": "I was charged twice for ORD-88219", "user_id": "U10492", "user_name": "alexmorgan", "channel": "C-support"},
        )
        assert normalized["provider"] == "slack"
        assert "charged twice" in normalized["complaint"].lower()
        assert normalized["customer_id"] == "U10492"
        assert normalized["customer_name"] == "alexmorgan"

    def test_normalize_slack_from_nested_event(self):
        normalized = normalize_webhook_payload(
            "slack",
            {"event": {"text": "My order never arrived", "user": "U07231"}, "type": "message"},
        )
        assert "never arrived" in normalized["complaint"].lower()
        assert normalized["customer_id"] == "U07231"

    # -- API webhook resolution ------------------------------------------

    def test_webhook_shopify(self, api_client):
        res = api_client.post(
            "/api/v1/saas/webhooks/shopify",
            json={
                "note": "I was charged twice for ORD-88219 USR-10492 TXN-9012",
                "name": "#1001",
                "order_number": 1001,
                "customer": {"id": "USR-10492", "email": "alex.morgan@gmail.com", "first_name": "Alex"},
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["provider"] == "shopify"
        assert "charged twice" in data["complaint"].lower()
        assert data["normalized"]["customer_id"] == "USR-10492"
        assert data["normalized"]["order_id"] == "#1001"
        assert data["customer_email"] == "alex.morgan@gmail.com"

    def test_webhook_shopify_cancel_reason(self, api_client):
        res = api_client.post(
            "/api/v1/saas/webhooks/shopify",
            json={"cancel_reason": "I was charged twice and want a refund", "name": "#2002", "customer": {"id": "USR-10492"}},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["provider"] == "shopify"
        assert "charged twice" in data["complaint"].lower()
        assert data["normalized"]["order_id"] == "#2002"

    def test_webhook_slack(self, api_client):
        res = api_client.post(
            "/api/v1/saas/webhooks/slack",
            json={"text": "I was charged twice for ORD-88219", "user_id": "U10492", "user_name": "alexmorgan"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["provider"] == "slack"
        assert "charged twice" in data["complaint"].lower()
        assert data["normalized"]["customer_id"] == "U10492"
        assert data["customer_name"] == "alexmorgan"

    def test_webhook_slack_with_tenant_header(self, api_client):
        tenant_id = _unique_tenant_id("slack-tenant")
        api_client.post("/api/v1/saas/tenants", json={"tenant_id": tenant_id, "config": {"product_name": "SlackTenantSaaS"}})
        res = api_client.post(
            "/api/v1/saas/webhooks/slack",
            json={"text": "How do I export my data?", "user_id": "U999", "user_name": "sam"},
            headers={"X-Tenant-ID": tenant_id},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["provider"] == "slack"
        assert data["product_name"] == "SlackTenantSaaS"
        assert data["tenant_id"] == tenant_id

    def test_webhook_shopify_with_tenant_query_param(self, api_client):
        tenant_id = _unique_tenant_id("shop-tenant")
        api_client.post("/api/v1/saas/tenants", json={"tenant_id": tenant_id, "config": {"product_name": "ShopifyTenantSaaS"}})
        res = api_client.post(
            f"/api/v1/saas/webhooks/shopify?tenant={tenant_id}",
            json={"note": "How do I export my data?", "name": "#3003", "customer": {"id": "USR-1"}},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["product_name"] == "ShopifyTenantSaaS"
        assert data["provider"] == "shopify"

    def test_info_advertises_shopify_and_slack(self, api_client):
        res = api_client.get("/api/v1/saas/info")
        data = res.json()
        assert "shopify" in data["capabilities"]["webhooks"]
        assert "slack" in data["capabilities"]["webhooks"]
        assert "shopify" in data["supported_providers"]
        assert "slack" in data["supported_providers"]
