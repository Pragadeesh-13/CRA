from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import database
from app.api.v1.saas_routes import create_saas_router
from app.core.tool_registry import TroyToolRegistry
from app.sdk.client import TroyClient


def test_repeated_tool_request_invokes_handler_once():
    registry = TroyToolRegistry()
    started, finish = Event(), Event()
    calls = []

    @registry.tool(name="credit")
    def credit(account):
        calls.append(account)
        started.set()
        assert finish.wait(5)
        return {"ok": True}

    def execute(account):
        return registry.execute("credit", {"account": account}, idempotency_key="event")

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(execute, "a")
        try:
            assert started.wait(5)
            assert execute("a")["ok"] is False
        finally:
            finish.set()
        assert first.result()["ok"]
    assert execute("a")["from_cache"]
    assert execute("b")["ok"] is False
    assert calls == ["a"]


@pytest.mark.parametrize("resolve", [database.execute_manual_resolution, database.execute_agent_resolution])
def test_console_requires_matching_duplicate(resolve, isolated_database):
    path = isolated_database["transactions_file"]
    before = path.read_bytes()
    result = resolve(customer_id="USR-00000", query="duplicate charge on ORD-88219", category="Refunds")
    assert result["ok"] is False
    assert path.read_bytes() == before


def test_http_execution_controls():
    app = FastAPI()
    app.include_router(create_saas_router(TroyClient()))
    client = TestClient(app)
    ticket = {"complaint": "How do I contact support?"}
    for control in [{"auto_execute": "sometimes"}, {"dryRun": 0},
                    {"dryRun": True, "dry_run": False},
                    {"config": {"policies": {"approval_required": False}}}]:
        assert client.post("/api/v1/saas/resolve", json={**ticket, **control}).status_code == 400
    data = client.post("/api/v1/saas/resolve", json={**ticket, "autoExecute": "false"}).json()
    assert data["auto_execute"] is False


@pytest.mark.parametrize("status,body,outcome", [(202, {"status": "Completed"}, "Accepted"),
                                                 (200, {}, "Unconfirmed"), (200, "OK", "Unconfirmed")])
def test_rest_action_requires_confirmation(status, body, outcome):
    import httpx
    from app.core.adapter import RestApiDataAdapter

    transport = httpx.MockTransport(lambda req: httpx.Response(status, json=body))
    with httpx.Client(transport=transport) as http:
        adapter = RestApiDataAdapter("https://example.test", http_client=http)
        assert adapter.execute_action("issue_refund", {})["status"] == outcome


def test_explicit_csv_paths_isolate_reads_and_writes(tmp_path, isolated_database):
    import shutil
    from app.core.adapter import CsvDataAdapter

    accounts = tmp_path / "custom_accounts.csv"
    transactions = tmp_path / "custom_transactions.csv"
    for custom, key in [(accounts, "accounts_file"), (transactions, "transactions_file")]:
        shutil.copyfile(isolated_database[key], custom)
    original = isolated_database["transactions_file"].read_bytes()
    adapter = CsvDataAdapter(transactions_file=transactions, accounts_file=accounts)
    assert adapter.execute_action("update_transaction", {"transaction_id": "TXN-9012",
                                                       "updates": {"notes": "private ledger"}})["ok"]
    assert any(row["notes"] == "private ledger" for row in adapter.get_transactions("USR-10492"))
    assert isolated_database["transactions_file"].read_bytes() == original


def test_tool_signature_preserves_keyword_only_arguments():
    registry = TroyToolRegistry()

    @registry.tool(name="invoice")
    def invoice(customer_id, *, invoice_id):
        return {"customer_id": customer_id, "invoice_id": invoice_id}

    params = {"customer_id": "customer-a", "invoice_id": "invoice-b"}
    assert registry.execute("invoice", params)["invoice_id"] == "invoice-b"
    assert registry.execute("invoice", {"customer_id": "customer-a"})["ok"] is False


def test_ticket_history_uses_session_tickets(monkeypatch):
    from app import main
    from app.simulation.state import SimulationState
    from types import SimpleNamespace

    state = SimulationState()
    ticket = {"title": "Invoice request", "description": "Send my invoice"}
    state.set_ticket(ticket)
    state.set_story(ticket)
    monkeypatch.setattr(main, "get_active_session", lambda _: SimpleNamespace(state=state))
    response = TestClient(main.app).get("/api/database/tickets").json()
    assert response["tickets"] == [ticket]
    assert response["count"] == 1


@pytest.mark.asyncio
async def test_key_vault_stores_only_sealed_key():
    from app.auth.key_vault import KeyVault

    vault = KeyVault()
    user = {"id": "example"}
    await vault.put(user, {"apiKey": "test-only-secret"})
    assert "test-only-secret" not in repr(vault._memory_vault)
    assert (await vault.get(user))["apiKey"] == "test-only-secret"


def test_webhook_relay_is_scoped_to_webhook_route(monkeypatch):
    from app import main
    monkeypatch.setattr(main.guard, "_mode", "required")
    monkeypatch.setattr(main.settings, "webhook_token", "relay-fixture")
    http = TestClient(main.app)
    url = "/api/v1/saas/webhooks/generic"
    data = {"complaint": "Please send my invoice"}
    headers = {"X-Troy-Webhook-Token": "relay-fixture"}
    assert http.post(url, json=data).status_code == 401
    assert http.post(url, json=data, headers=headers).status_code == 200
    assert http.post("/api/v1/saas/resolve", json=data, headers=headers).status_code == 401


def test_relay_accepts_a_router_mounted_under_a_prefix(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "webhook_token", "relay-fixture")
    app = FastAPI()
    app.include_router(create_saas_router(TroyClient()), prefix="/support")
    client = TestClient(app)
    path = "/support/api/v1/saas/webhooks/generic"
    ticket = {"complaint": "Please send my invoice"}
    assert client.post(path, json=ticket).status_code == 401
    result = client.post(path, json=ticket, headers={"X-Troy-Webhook-Token": "relay-fixture"})
    assert result.status_code == 200
    assert result.json()["status"] == "needs_review"


@pytest.fixture
def webhook_invoice_client(monkeypatch):
    from app.config import settings
    from app.core.adapter import InMemoryDataAdapter

    monkeypatch.setattr(settings, "webhook_token", "relay-fixture")
    adapter = InMemoryDataAdapter(customers=[{"customer_id": "USR-10492", "invoice_id": "INV-20"}])
    agent = TroyClient(adapter=adapter, driver=object())
    calls = []

    @agent.tool(name="send_invoice")
    def send_invoice(**params):
        calls.append(params)
        return {"status": "Sent"}

    app = FastAPI()
    app.include_router(create_saas_router(agent))
    return TestClient(app), calls


@pytest.mark.parametrize("provider,body,delivery_headers", [
    ("zendesk", {"ticket": {"id": 42, "description": "Please send invoice INV-20", "customer_id": "USR-10492"}},
     {"X-Troy-Webhook-Event-Id": "delivery-one"}),
    ("shopify", {"note": "Please send invoice INV-20", "customer_id": "USR-10492"},
     {"X-Shopify-Event-Id": "delivery-one"}),
    ("slack", {"type": "event_callback", "event_id": "delivery-one",
               "event": {"text": "Please send invoice INV-20", "user": "USR-10492"}}, {}),
    ("intercom", {"type": "notification_event", "id": "delivery-one",
                  "data": {"item": {"id": 42, "body": "Please send invoice INV-20", "customer_id": "USR-10492"}}}, {}),
    ("stripe", {"object": "event", "id": "delivery-one", "type": "invoice.created",
                "data": {"object": {"description": "Please send invoice INV-20", "customer": "USR-10492"}}}, {}),
])
def test_verified_event_reuses_business_tool_result(webhook_invoice_client, provider, body, delivery_headers):
    client, calls = webhook_invoice_client
    headers = {"X-Troy-Webhook-Token": "relay-fixture", **delivery_headers}
    path = f"/api/v1/saas/webhooks/{provider}"

    first = client.post(path, json=body, headers=headers).json()
    replay = client.post(path, json=body, headers=headers).json()
    assert first["status"] == replay["status"] == "resolved"
    assert replay["action_result"]["idempotent_replay"] is True

    # The same ticket and action with a new event must still execute.
    distinct_headers = {key: "delivery-two" for key in delivery_headers}
    distinct = client.post(path, json={
        **body, **{key: "delivery-two" for key in ("id", "event_id") if key in body},
    }, headers={**headers, **distinct_headers}).json()
    assert distinct["action_result"]["idempotency_key"] == f"webhook:{provider}:delivery-two"
    assert calls == [{"customer_id": "USR-10492", "invoice_id": "INV-20"}] * 2


def test_webhook_replay_namespaces_and_caller_keys(webhook_invoice_client):
    client, calls = webhook_invoice_client
    body = {"complaint": "Please send invoice INV-20", "customer_id": "USR-10492"}
    headers = {"X-Troy-Webhook-Token": "relay-fixture", "X-Troy-Webhook-Event-Id": "shared-event"}
    for provider in ("zendesk", "freshdesk"):
        path = f"/api/v1/saas/webhooks/{provider}"
        assert client.post(path, json=body, headers=headers).json()["status"] == "resolved"
    path = "/api/v1/saas/webhooks/generic"
    for key_headers, key_body, expected in [
        ({"Idempotency-Key": "caller-header"}, {}, "caller-header"),
        ({}, {"idempotencyKey": "caller-body"}, "caller-body"),
        ({}, {"context": {"idempotency_key": "caller-context"}}, "caller-context"),
    ]:
        result = client.post(path, json={**body, **key_body}, headers={**headers, **key_headers}).json()
        assert result["action_result"]["idempotency_key"] == expected
    assert len(calls) == 5


def test_webhook_ignores_unverified_delivery_headers_and_ticket_ids(webhook_invoice_client, monkeypatch):
    from app.config import settings

    client, calls = webhook_invoice_client
    body = {"ticket": {"id": 42, "description": "Please send invoice INV-20", "customer_id": "USR-10492"},
            "context": {"event_id": "unverified-context"}}
    path = "/api/v1/saas/webhooks/zendesk"
    for _ in range(2):
        assert client.post(path, json=body, headers={"X-Troy-Webhook-Token": "relay-fixture"}).json()["status"] == "resolved"
    monkeypatch.setattr(settings, "webhook_token", "")
    for _ in range(2):
        assert client.post(path, json=body, headers={"X-Troy-Webhook-Event-Id": "unverified"}).json()["status"] == "resolved"
    assert len(calls) == 4


def test_explicit_csv_updates_keep_host_business_fields(tmp_path):
    from app.core.adapter import CsvDataAdapter

    accounts = tmp_path / "accounts.csv"
    accounts.write_text(
        "customer_id,customer_name,invoice_id,event_id\nUSR-10492,Before,INV-20,EVT-10\n",
        encoding="utf-8",
    )
    transactions = tmp_path / "transactions.csv"
    transactions.write_text(
        "transaction_id,customer_id,order_id,host_payment_id\nTXN-9012,USR-10492,ORD-88219,pay-example\n",
        encoding="utf-8",
    )
    adapter = CsvDataAdapter(transactions_file=transactions, accounts_file=accounts)
    agent = TroyClient(adapter=adapter, driver=object())

    @agent.tool(name="send_invoice")
    def send_invoice(customer_id, invoice_id):
        assert (customer_id, invoice_id) == ("USR-10492", "INV-20")
        return {"status": "Sent"}

    assert adapter.execute_action("update_customer_account", {
        "customer_id": "USR-10492", "updates": {"customer_name": "After"},
    })["ok"]
    assert adapter.execute_action("update_transaction", {
        "transaction_id": "TXN-9012", "updates": {"notes": "Updated locally"},
    })["ok"]
    account = adapter.get_customer("USR-10492")
    assert (account["customer_name"], account["invoice_id"], account["event_id"]) == ("After", "INV-20", "EVT-10")
    transaction, = adapter.get_transactions("USR-10492")
    assert (transaction["notes"], transaction["host_payment_id"]) == ("Updated locally", "pay-example")
    assert agent.resolve_ticket_sync("Please send invoice INV-20 for USR-10492")["status"] == "resolved"


@pytest.mark.parametrize("remote_outcome", ["acknowledged", "read_timeout"])
def test_sdk_reports_unconfirmed_rest_refund_without_completion_status(remote_outcome):
    import httpx
    from app.core.adapter import RestApiDataAdapter
    from test_troy_opensource_tool import DUPLICATE, _seeded_adapter

    data = _seeded_adapter()
    dispatches = []

    def handle(request):
        if request.url.path.startswith("/customers/"):
            return httpx.Response(200, json=data.get_customer("USR-10492"))
        if request.url.path == "/transactions":
            return httpx.Response(200, json=data.get_transactions("USR-10492"))
        if request.url.path.startswith("/actions/"):
            dispatches.append(request.url.path)
            if remote_outcome == "read_timeout":
                raise httpx.ReadTimeout("Synthetic response timeout", request=request)
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        adapter = RestApiDataAdapter("https://example.test", http_client=http)
        result = TroyClient(adapter=adapter, driver=object()).resolve_ticket_sync(DUPLICATE)
    assert len(dispatches) == 1
    assert result["status"] == "action_unconfirmed"
    assert result["action_status"] == "Unconfirmed"
    assert result["refund_id"] is None
    assert "could not be confirmed" in result["summary"]


def test_rest_customer_identifier_stays_in_one_path_segment():
    import httpx
    from app.core.adapter import RestApiDataAdapter

    paths = []
    customer_id = "workspace/east #17"

    def handle(request):
        paths.append(request.url.raw_path)
        return httpx.Response(200, json={"customer_id": customer_id})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        adapter = RestApiDataAdapter("https://example.test", http_client=http)
        assert adapter.get_customer(customer_id)["customer_id"] == customer_id
    assert paths[0] == b"/customers/workspace%2Feast%20%2317"
