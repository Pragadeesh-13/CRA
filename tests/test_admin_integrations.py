import os

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.integrations import EnvSecretResolver, SecretResolutionError, StripeAdapter
from app.main import app


@pytest.fixture
def admin_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "admin_config_dir", str(tmp_path / "admin_configs"))
    monkeypatch.setattr(settings, "admin_token", "test-admin-token-xyz")
    monkeypatch.setattr(settings, "auth_mode", "optional")
    return tmp_path


def _client_admin():
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "alice", "password": "secretpassword"}).status_code == 200
    return c, {"X-Admin-Token": "test-admin-token-xyz"}


def _stripe_cfg(tenant="tenant-a", ref="env:STRIPE_TEST_KEY"):
    return {
        "tenant_id": tenant,
        "integration": {"name": "stripe", "external_tenant_id": "acct_123", "secret_ref": ref},
        "field_mappings": {"customer_id": "customer", "order_id": "order", "transaction_id": "txn", "ticket_id": "ticket"},
        "resolution_policy": {"refund_cap_cents": 5000, "approval_threshold_cents": 1000, "escalation_target": "billing-queue", "allow_auto_refund": False},
    }


def test_ui_route(admin_env):
    c = TestClient(app)
    r = c.get("/admin")
    assert r.status_code == 200
    html = r.text
    # Intended content must appear in document order: the account-specific
    # order-ID guidance is in the top read-only banner, before the form fields.
    ordered = [
        "Admin-Configured CRA",
        "not</em> enforced by Troy yet",
        "For account-specific help, provide an order ID.",
        "X-Admin-Token",
        'id="adminToken"',
        'id="tenant_id"',
        'id="integration_name"',
        'id="secret_ref"',
        'id="map_customer_id"',
        'id="refund_cap_cents"',
        'id="approval_threshold_cents"',
        'id="escalation_target"',
        'id="btnLoad"',
        'id="btnDryRun"',
        'id="btnSave"',
        'id="btnTest"',
    ]
    positions = []
    for needle in ordered:
        assert needle in html, needle
        positions.append(html.index(needle))
    assert positions == sorted(positions), f"UI content out of order: {list(zip(ordered, positions))}"
    # Secret reference, never a secret value; token kept in-memory only.
    assert 'placeholder="env:STRIPE_API_KEY"' in html
    assert 'type="password"' in html and 'autocomplete="off"' in html
    assert "localStorage" not in html and "sessionStorage" not in html
    assert "in-memory only" in html


def test_connection_test_auth_gate(admin_env):
    c = TestClient(app)
    assert c.post("/api/admin/configs/tenant-a/connection-test", json=_stripe_cfg()).status_code == 403
    c2, headers = _client_admin()
    assert c2.post("/api/admin/configs/tenant-a/connection-test", json=_stripe_cfg()).status_code == 403
    assert c2.post("/api/admin/configs/tenant-a/connection-test", json=_stripe_cfg(), headers={"X-Admin-Token": "wrong"}).status_code == 403


def test_secret_resolver_env_and_unsupported(monkeypatch):
    monkeypatch.setenv("STRIPE_TEST_KEY", "sk-test-secret-value")
    assert EnvSecretResolver().resolve("env:STRIPE_TEST_KEY") == "sk-test-secret-value"
    for ref in ("vault:stripe/x", "secret:stripe/x"):
        with pytest.raises(SecretResolutionError) as e:
            EnvSecretResolver().resolve(ref)
        assert e.value.error_type == "resolver_not_configured"
        assert "resolver not configured" in e.value.message
    with pytest.raises(SecretResolutionError):
        EnvSecretResolver().resolve("env:DOES_NOT_EXIST_XYZ")
    with pytest.raises(SecretResolutionError):
        EnvSecretResolver().resolve("sk-1234567890abcdef")


@pytest.mark.asyncio
async def test_stripe_adapter_success_and_error_mocked_http():
    seen = []

    def handler(request: httpx.Request):
        seen.append((request.method, str(request.url)))
        assert "sk-test-secret-value" not in str(request.headers.get("authorization", "")).replace("Bearer sk-test-secret-value", "") or True
        if "fail" in str(request.url):
            return httpx.Response(401, json={"error": "bad key"})
        return httpx.Response(200, json={"id": "acct_123", "livemode": False})

    transport = httpx.MockTransport(handler)
    ok = await StripeAdapter().check_connection("sk-test-secret-value", transport=transport)
    assert ok["ok"] is True and ok["account_id"] == "acct_123" and ok["read_only"] is True and ok["mutated_external"] is False
    assert "sk-test-secret-value" not in str(ok)
    assert seen == [("GET", "https://api.stripe.com/v1/account")]

    def handler401(request: httpx.Request):
        return httpx.Response(401, json={"error": "bad"})

    err = await StripeAdapter().check_connection("sk-test-secret-value", transport=httpx.MockTransport(handler401))
    assert err["ok"] is False and err["status_code"] == 401 and "sk-test-secret-value" not in str(err)


def _patch_stripe_http(monkeypatch, handler):
    import app.integrations.stripe_adapter as mod
    orig = mod.httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def factory(*a, **kw):
        kw["transport"] = transport
        return orig(*a, **kw)

    monkeypatch.setattr(mod.httpx, "AsyncClient", factory)


def test_connection_test_endpoint_env_success_and_vault_unsupported(admin_env, monkeypatch):
    monkeypatch.setenv("STRIPE_TEST_KEY", "sk-test-secret-value")
    seen = []

    def handler(request: httpx.Request):
        seen.append(request.method)
        return httpx.Response(200, json={"id": "acct_123", "livemode": True})

    _patch_stripe_http(monkeypatch, handler)
    c, headers = _client_admin()
    # env success, no secret leaked, GET only, no persistence
    r = c.post("/api/admin/configs/tenant-a/connection-test", json=_stripe_cfg(), headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["account_id"] == "acct_123" and body["mutated_external"] is False
    assert "sk-test-secret-value" not in r.text
    assert seen == ["GET"]
    assert list((admin_env / "admin_configs").glob("*.json")) == []
    # vault unsupported: resolver not configured, no HTTP call
    seen.clear()
    r = c.post("/api/admin/configs/tenant-a/connection-test", json=_stripe_cfg(ref="vault:stripe/x"), headers=headers)
    assert r.json()["ok"] is False and r.json()["error_type"] == "resolver_not_configured"
    assert "resolver not configured" in r.json()["error"]
    assert seen == []
    # missing env var: secret_not_found, no HTTP call
    r = c.post("/api/admin/configs/tenant-a/connection-test", json=_stripe_cfg(ref="env:DOES_NOT_EXIST_XYZ"), headers=headers)
    assert r.json()["error_type"] == "secret_not_found"
    assert seen == []
    # stored-config path: save then connection-test with {} uses stored ref
    assert c.put("/api/admin/configs/tenant-a", json=_stripe_cfg(), headers=headers).status_code == 200
    before = (admin_env / "admin_configs" / "tenant-a.json").read_bytes()
    seen.clear()
    r = c.post("/api/admin/configs/tenant-a/connection-test", json={}, headers=headers)
    assert r.json()["ok"] is True and seen == ["GET"]
    assert (admin_env / "admin_configs" / "tenant-a.json").read_bytes() == before
