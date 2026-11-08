import copy
import json

import pytest
from fastapi.testclient import TestClient

from app.admin_config import AdminConfigStore, validate_config, dry_run
from app.config import settings
from app.main import app


def _valid(tenant="tenant-a"):
    return {
        "tenant_id": tenant,
        "integration": {"name": "stripe", "external_tenant_id": "acct_123", "secret_ref": "vault:stripe/tenant-a"},
        "field_mappings": {"customer_id": "customer", "order_id": "order", "transaction_id": "txn", "ticket_id": "ticket"},
        "resolution_policy": {"refund_cap_cents": 5000, "approval_threshold_cents": 1000, "escalation_target": "billing-queue", "allow_auto_refund": False},
    }


@pytest.fixture
def admin_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "admin_config_dir", str(tmp_path / "admin_configs"))
    monkeypatch.setattr(settings, "admin_token", "test-admin-token-xyz")
    monkeypatch.setattr(settings, "auth_mode", "optional")
    return tmp_path


def _client_admin():
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": "alice", "password": "secretpassword"})
    assert r.status_code == 200
    return c, {"X-Admin-Token": "test-admin-token-xyz"}


def test_validation_accepts_valid_and_rejects_bad_policy():
    assert validate_config(_valid()) == []
    bad = _valid(); bad["resolution_policy"]["approval_threshold_cents"] = 99999
    assert any("approval_threshold" in e for e in validate_config(bad))
    bad2 = _valid(); bad2["field_mappings"].pop("customer_id")
    assert any("customer_id" in e for e in validate_config(bad2))
    bad3 = _valid(); bad3["resolution_policy"]["refund_cap_cents"] = -1
    assert any("refund_cap" in e for e in validate_config(bad3))
    bad4 = _valid(); bad4["integration"]["name"] = "unknown-saas"
    assert any("integration.name" in e for e in validate_config(bad4))


def test_secret_rejection():
    for field, val in [("api_key", "sk-1234567890abcdef"), ("password", "hunter2"), ("access_token", "x")]:
        cfg = _valid(); cfg["integration"][field] = val
        errs = validate_config(cfg)
        assert any("secret_ref" in e.lower() or "credential" in e.lower() for e in errs), (field, errs)
    cfg = _valid(); cfg["integration"]["secret_ref"] = "sk-1234567890abcdef"
    assert any("secret_ref" in e for e in validate_config(cfg))
    cfg = _valid(); cfg["integration"]["secret_ref"] = "vault:stripe/ok"
    assert validate_config(cfg) == []


def test_tenant_isolation_store(tmp_path):
    store = AdminConfigStore(tmp_path / "cfg")
    store.save(_valid("tenant-a")); store.save(_valid("tenant-b"))
    assert store.load("tenant-a")["tenant_id"] == "tenant-a"
    assert store.load("tenant-b")["tenant_id"] == "tenant-b"
    assert store.load("tenant-c") is None
    # cross-tenant mismatch rejected
    assert validate_config(_valid("tenant-a"), expected_tenant="tenant-b")
    # path traversal rejected
    with pytest.raises(ValueError):
        store.path_for("../evil")


def test_auth_enforcement_fail_closed(admin_env):
    c = TestClient(app)
    # unauthenticated (optional mode, no session/token) -> 403, nothing persisted
    r = c.put("/api/admin/configs/tenant-a", json=_valid())
    assert r.status_code == 403
    # authenticated but no admin token -> 403
    c2, _ = _client_admin()
    r2 = c2.put("/api/admin/configs/tenant-a", json=_valid())
    assert r2.status_code == 403
    # wrong token -> 403
    r3 = c2.put("/api/admin/configs/tenant-a", json=_valid(), headers={"X-Admin-Token": "wrong"})
    assert r3.status_code == 403
    assert not (admin_env / "admin_configs").exists() or list((admin_env / "admin_configs").glob("*.json")) == []


def test_put_get_and_tenant_isolation_http(admin_env):
    c, headers = _client_admin()
    r = c.put("/api/admin/configs/tenant-a", json=_valid("tenant-a"), headers=headers)
    assert r.status_code == 200 and r.json()["ok"] is True
    r = c.get("/api/admin/configs/tenant-a", headers=headers)
    assert r.status_code == 200 and r.json()["config"]["tenant_id"] == "tenant-a"
    r = c.get("/api/admin/configs/tenant-b", headers=headers)
    assert r.status_code == 404
    # secret in PUT rejected, nothing overwritten
    bad = _valid("tenant-a"); bad["integration"]["api_key"] = "sk-1234567890abcdef"
    r = c.put("/api/admin/configs/tenant-a", json=bad, headers=headers)
    assert r.status_code == 400
    assert c.get("/api/admin/configs/tenant-a", headers=headers).json()["config"]["integration"]["secret_ref"] == "vault:stripe/tenant-a"


def test_dry_run_no_mutation(admin_env):
    c, headers = _client_admin()
    # dry-run with submitted config: valid, no persistence, no external contact
    r = c.post("/api/admin/configs/tenant-a/dry-run", json=_valid("tenant-a"), headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True and body["external_contact"] is False and body["mutated_external"] is False and body["persisted"] is False
    assert list((admin_env / "admin_configs").glob("*.json")) == []
    # persist, then dry-run stored: file hash unchanged
    assert c.put("/api/admin/configs/tenant-a", json=_valid("tenant-a"), headers=headers).status_code == 200
    p = admin_env / "admin_configs" / "tenant-a.json"
    before = p.read_bytes()
    r = c.post("/api/admin/configs/tenant-a/dry-run", json={}, headers=headers)
    assert r.json()["valid"] is True
    assert p.read_bytes() == before
    # invalid dry-run reports errors without persisting
    bad = _valid("tenant-b"); bad["resolution_policy"]["refund_cap_cents"] = 0
    r = c.post("/api/admin/configs/tenant-b/dry-run", json=bad, headers=headers)
    assert r.json()["valid"] is False and r.json()["errors"]
    assert not (admin_env / "admin_configs" / "tenant-b.json").exists()
    # unit dry_run also pure
    assert dry_run(_valid())["valid"] is True
