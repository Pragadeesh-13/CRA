import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.saas_routes import create_saas_router
from app.config import settings
from app.sdk import TroyClient, create_troy_router


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(settings, "webhook_token", "")
    agent = TroyClient(driver=object())
    calls = []

    async def resolve(complaint, **options):
        calls.append(("resolve", complaint, options))
        return {"ok": True, "status": "resolved"}

    @agent.tool(name="record_example")
    def record_example(**params):
        calls.append(("tool", params))
        return {"ok": True}

    monkeypatch.setattr(agent, "resolve_ticket", resolve)
    app = FastAPI()
    app.include_router(create_saas_router(agent))
    app.include_router(create_troy_router(agent, prefix="/sdk"))
    return TestClient(app, raise_server_exceptions=False), calls


@pytest.mark.parametrize("path", ["/api/v1/saas/resolve", "/api/v1/saas/webhooks/stripe", "/sdk/resolve", "/sdk/tools/execute"])
@pytest.mark.parametrize("body", ["", "{", "[]", '"ticket"', "null", '{"unused":NaN}', '{"unused":1e309}'])
def test_bad_json_envelope_fails_before_dispatch(api, path, body):
    http, calls = api
    result = http.post(path, content=body, headers={"Content-Type": "application/json"})
    assert result.status_code == 400
    assert result.json()["error"] == "Request body must be a JSON object"
    assert calls == []


@pytest.mark.parametrize("path", ["/api/v1/saas/resolve", "/sdk/resolve"])
@pytest.mark.parametrize("bad_field", [
    {"complaint": ["invoice"]}, {"context": []}, {"customerId": True}, {"order_id": {}},
    {"transactionId": 0.25}, {"idempotencyKey": []}, {"context": {"customerId": ["USR-20"]}},
    {"context": {"invoice_id": {"id": "INV-20"}}}, {"context": {"credit_amount": True}},
])
def test_resolve_rejects_bad_input_types(api, path, bad_field):
    http, calls = api
    result = http.post(path, json={"complaint": "Please send invoice INV-20", **bad_field})
    assert result.status_code == 400
    assert calls == []


@pytest.mark.parametrize("bad_field", [
    {"tool": ["record_example"]}, {"params": []}, {"args": False},
    {"arguments": "not an object"}, {"idempotent_key": {}},
])
def test_tool_does_not_dispatch_bad_envelopes(api, bad_field):
    http, calls = api
    result = http.post("/sdk/tools/execute", json={"tool": "record_example", **bad_field})
    assert result.status_code == 400
    assert calls == []


@pytest.mark.parametrize(("provider", "payload"), [
    ("stripe", {}),
    ("stripe", {"type": "charge.refunded", "data": []}),
    ("stripe", {"type": "charge.refunded", "data": {"object": {"id": "ch_example", "amount": True}}}),
    ("zendesk", {"ticket": {"description": ["Please send invoice INV-20"]}}),
    ("intercom", {"data": {"item": {"body": {"text": "Please send invoice INV-20"}}}}),
    ("generic", {"subject": {"text": "Example"}}),
    ("generic", {"complaint": "Example", "context": []}),
    ("shopify", {"note": "Example", "customer": {"id": ["USR-20"]}}),
    ("shopify", {"note": "Example", "customer": {"id": False}}),
])
def test_webhook_rejects_invalid_provider_fields(api, provider, payload):
    http, calls = api
    result = http.post(f"/api/v1/saas/webhooks/{provider}", json=payload)
    assert result.status_code == 400
    assert calls == []


def test_valid_aliases_provider_envelopes_and_open_tool_params(api):
    http, calls = api
    for path in ("/api/v1/saas/resolve", "/sdk/resolve"):
        valid = http.post(path, json={
            "message": "Please send invoice INV-20", "customerId": 42, "orderId": "#4",
            "transactionId": "TXN-1", "idempotencyKey": "request-key", "dryRun": "true",
            "context": {"invoice_id": "INV-20", "other": {"tag": [1, True]}},
        })
        assert valid.status_code == 200
        options = calls[-1][2]
        assert options["customer_id"] == 42
        assert options["order_id"] == "#4"
        assert options["transaction_id"] == "TXN-1"
        assert options["idempotency_key"] == "request-key"
        assert options["dry_run"] is True
        assert options["context"]["other"] == {"tag": [1, True]}

    result = http.post("/api/v1/saas/webhooks/slack", json={
        "message": {"text": "Please send invoice INV-20", "user": "USR-20"},
    })
    assert result.status_code == 200
    assert calls[-1][1] == "Please send invoice INV-20"

    result = http.post("/api/v1/saas/webhooks/stripe", json={
        "object": "event", "type": "charge.created", "data": {"object": {"id": "ch_example", "amount": 500}},
    })
    assert result.status_code == 200

    result = http.post("/api/v1/saas/webhooks/stripe", json={
        "object": "charge", "id": "ch_expanded", "customer": {"id": "cus_example"}, "amount": 500,
    })
    assert result.status_code == 200
    assert calls[-1][2]["customer_id"] == "cus_example"

    result = http.post("/api/v1/saas/webhooks/freshdesk", json={
        "ticket": {"description": "Example", "status": 2, "requester_id": 42},
    })
    assert result.status_code == 200
    assert result.json()["normalized"]["event_type"] == 2

    result = http.post("/api/v1/saas/webhooks/zendesk", json={
        "ticket": {"description": "Example", "transaction_id": "TXN-1", "requester_id": 42},
    })
    assert result.status_code == 200
    assert calls[-1][2]["customer_id"] == 42
    assert calls[-1][2]["transaction_id"] == "TXN-1"

    result = http.post("/sdk/tools/execute", json={
        "toolName": "record_example", "arguments": {"payload": ["example", 1.25, True]},
        "idempotent_key": "tool-example",
    })
    assert result.json()["ok"] is True
    assert calls[-1] == ("tool", {"payload": ["example", 1.25, True]})


def test_verified_relay_rejects_malformed_native_delivery_id(api, monkeypatch):
    http, calls = api
    monkeypatch.setattr(settings, "webhook_token", "synthetic-relay")
    result = http.post("/api/v1/saas/webhooks/slack", headers={"X-Troy-Webhook-Token": "synthetic-relay"},
                       json={"type": "event_callback", "event_id": [], "event": {"text": "Example"}})
    assert result.status_code == 400
    assert "webhook event ID" in result.json()["error"]
    assert calls == []
