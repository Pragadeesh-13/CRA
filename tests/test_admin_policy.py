import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from app.admin_config import AdminConfigStore, evaluate_policy
from app.config import settings
from app.http.sse_manager import SseManager
from app.providers.mock_driver import MockDriver
from app.simulation.state import SimulationState
from app.simulation.troy import Troy


def _valid(tenant="tenant-a", cap=5000, thresh=1000):
    return {
        "tenant_id": tenant,
        "integration": {"name": "stripe", "external_tenant_id": "acct_123", "secret_ref": "env:STRIPE_TEST_KEY"},
        "field_mappings": {"customer_id": "customer", "order_id": "order", "transaction_id": "txn", "ticket_id": "ticket"},
        "resolution_policy": {"refund_cap_cents": cap, "approval_threshold_cents": thresh, "escalation_target": "billing-queue", "allow_auto_refund": False},
    }


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class CapturingSse(SseManager):
    def __init__(self):
        super().__init__()
        self.events: List[Dict[str, Any]] = []

    def broadcast(self, event: str, data: Any):
        self.events.append({"event": event, "data": data})
        return super().broadcast(event, data)


def _troy(sse=None, tmp_path=None, monkeypatch=None):
    if tmp_path is not None and monkeypatch is not None:
        monkeypatch.setattr(settings, "admin_config_dir", str(tmp_path / "admin_configs"))
    return Troy(config=settings, driver=MockDriver(), sse_manager=sse or CapturingSse(), state=SimulationState(), enable_barrier=False)


# ── Pure policy layer ────────────────────────────────────────────────────────

def test_policy_below_cap_allow():
    r = evaluate_policy(_valid(cap=5000, thresh=1000), tenant_id="tenant-a", action="duplicate_refund", amount_cents=500)
    assert r["decision"] == "allow" and r["allowed"] is True
    assert r["mutated_external"] is False and r["mutated_customer_data"] is False and r["external_contact"] is False


def test_policy_over_cap_deny_escalates():
    r = evaluate_policy(_valid(cap=5000, thresh=1000), tenant_id="tenant-a", action="duplicate_refund", amount_cents=9000)
    assert r["decision"] == "deny" and r["allowed"] is False
    assert r["escalation_target"] == "billing-queue" and r["escalated_to"] == "billing-queue"


def test_policy_approval_threshold():
    r = evaluate_policy(_valid(cap=5000, thresh=1000), tenant_id="tenant-a", action="duplicate_refund", amount_cents=2000)
    assert r["decision"] == "require_approval" and r["allowed"] is False and r["escalated_to"] == "billing-queue"


def test_policy_dry_run():
    r = evaluate_policy(_valid(), tenant_id="tenant-a", action="duplicate_refund", amount_cents=500, dry_run=True)
    assert r["decision"] == "dry_run" and r["allowed"] is False and r["dry_run"] is True


def test_policy_wrong_tenant_and_unknown():
    assert evaluate_policy(_valid("tenant-a"), tenant_id="tenant-b", action="duplicate_refund", amount_cents=100)["decision"] == "deny"
    assert evaluate_policy(_valid(), tenant_id="tenant-a", action="unknown_action", amount_cents=100)["decision"] == "deny"
    assert evaluate_policy(_valid(), tenant_id="tenant-a", action="duplicate_refund", amount_cents=None)["decision"] == "deny"
    assert evaluate_policy(_valid(), tenant_id="tenant-a", action="duplicate_refund", amount_cents="49")["decision"] == "deny"
    assert evaluate_policy(None, tenant_id="tenant-a", action="duplicate_refund", amount_cents=100)["decision"] == "deny"
    assert evaluate_policy(_valid(), tenant_id=None, action="duplicate_refund", amount_cents=100)["decision"] == "deny"


# ── Troy integration (isolated DB, no side effects) ──────────────────────────

@pytest.mark.asyncio
async def test_troy_missing_tenant_no_side_effect(isolated_database, tmp_path, monkeypatch):
    tx, acc = isolated_database["transactions_file"], isolated_database["accounts_file"]
    before = (_sha(tx), _sha(acc), tx.read_bytes(), acc.read_bytes())
    sse = CapturingSse()
    troy = _troy(sse, tmp_path, monkeypatch)
    ok = await troy.run(
        {"title": "Dup", "description": "I was debited twice $49.00 for order ORD-81094. Duplicate charge TXN-7712 needs refund.", "category": "Refunds"},
        driver=MockDriver(),
    )
    assert ok is True
    assert before == (_sha(tx), _sha(acc), tx.read_bytes(), acc.read_bytes())
    assert "database_updated" not in [e["event"] for e in sse.events]
    res_end = next(e for e in sse.events if e["event"] == "resolution_end")
    pd = res_end["data"].get("policy_decision")
    assert pd and pd["decision"] == "deny" and "missing trusted tenant" in pd["rationale"]
    assert "ALREADY REFUNDED" not in res_end["data"]["full_resolution"]


@pytest.mark.asyncio
async def test_troy_tenant_policy_allow_is_simulated_no_mutation(isolated_database, tmp_path, monkeypatch):
    # config cap 5000, thresh 1000; duplicate is $49 -> 4900c > thresh => require_approval, not allow.
    # Use thresh 5000 config so 4900 is below threshold -> allow simulated.
    store = AdminConfigStore(tmp_path / "admin_configs")
    store.save(_valid("tenant-a", cap=5000, thresh=5000))
    tx, acc = isolated_database["transactions_file"], isolated_database["accounts_file"]
    before = (_sha(tx), _sha(acc))
    sse = CapturingSse()
    troy = _troy(sse, tmp_path, monkeypatch)
    ok = await troy.run(
        {"title": "Dup", "description": "I was debited twice $49.00 for order ORD-81094. Duplicate charge TXN-7712 needs refund.", "category": "Refunds", "tenant_id": "tenant-a"},
        driver=MockDriver(),
    )
    assert ok is True
    assert before == (_sha(tx), _sha(acc))
    assert "database_updated" not in [e["event"] for e in sse.events]
    res_end = next(e for e in sse.events if e["event"] == "resolution_end")
    assert res_end["data"]["policy_decision"]["decision"] == "allow"
    assert "REF-81094" not in res_end["data"]["full_resolution"]


@pytest.mark.asyncio
async def test_troy_tenant_over_cap_and_wrong_tenant(isolated_database, tmp_path, monkeypatch):
    store = AdminConfigStore(tmp_path / "admin_configs")
    store.save(_valid("tenant-a", cap=1000, thresh=500))  # $49=4900 > cap
    tx, acc = isolated_database["transactions_file"], isolated_database["accounts_file"]
    before = (_sha(tx), _sha(acc))
    for tenant in ("tenant-a", "tenant-b"):
        sse = CapturingSse()
        troy = _troy(sse, tmp_path, monkeypatch)
        # tenant-b has no config -> fail closed; tenant-a over cap -> deny/escalate
        ok = await troy.run(
            {"title": "Dup", "description": "I was debited twice $49.00 for order ORD-81094. Duplicate charge TXN-7712 needs refund.", "category": "Refunds", "tenant_id": tenant},
            driver=MockDriver(),
        )
        assert ok is True
        res_end = next(e for e in sse.events if e["event"] == "resolution_end")
        pd = res_end["data"]["policy_decision"]
        assert pd["decision"] == "deny"
        if tenant == "tenant-a":
            assert pd["escalation_target"] == "billing-queue" and pd["escalated_to"] == "billing-queue"
    assert before == (_sha(tx), _sha(acc))
    assert "database_updated" not in [e["event"] for e in sse.events]


def test_resolve_action_fail_closed_no_mutation(isolated_database):
    """Legacy bypass route must not mutate CSV or claim success (admin fork)."""
    from fastapi.testclient import TestClient
    from app.main import app as _app

    tx, acc = isolated_database["transactions_file"], isolated_database["accounts_file"]
    before = (_sha(tx), _sha(acc), tx.read_bytes(), acc.read_bytes())
    c = TestClient(_app)
    for payload in (
        {"action": "agent_resolve", "customer_id": "USR-10492", "query": "Order #88219 charged twice", "action_details": "Refund Initiated ($49.00 via Stripe API)"},
        {"action": "manual_resolve", "customer_id": "USR-08914", "notes": "bypass attempt"},
        {"action": "agent_resolve", "customer_id": "USR-07231", "query": "ORD-81094", "tenant_id": "tenant-a"},
    ):
        r = c.post("/api/resolve/action", json=payload)
        assert r.status_code == 403, r.text
        body = r.json()
        assert body["ok"] is False and body["decision"] == "deny"
        assert body["mutated_customer_data"] is False and body["mutated_external"] is False
        assert "refund_id" not in body
    assert before == (_sha(tx), _sha(acc), tx.read_bytes(), acc.read_bytes())


@pytest.mark.asyncio
async def test_troy_tenant_dry_run_and_approval(isolated_database, tmp_path, monkeypatch):
    store = AdminConfigStore(tmp_path / "admin_configs")
    store.save(_valid("tenant-a", cap=5000, thresh=1000))
    tx, acc = isolated_database["transactions_file"], isolated_database["accounts_file"]
    before = (_sha(tx), _sha(acc))
    # dry_run with small amount would allow, but dry_run decision must not execute (4900 > thresh, dry_run path)
    sse = CapturingSse()
    troy = _troy(sse, tmp_path, monkeypatch)
    await troy.run(
        {"title": "Dup", "description": "I was debited twice $49.00 for order ORD-81094. Duplicate charge TXN-7712 needs refund.", "category": "Refunds", "tenant_id": "tenant-a", "dry_run": True},
        driver=MockDriver(),
    )
    res_end = next(e for e in sse.events if e["event"] == "resolution_end")
    assert res_end["data"]["policy_decision"]["decision"] == "dry_run"
    # approval threshold (no dry_run)
    sse2 = CapturingSse()
    troy2 = _troy(sse2, tmp_path, monkeypatch)
    await troy2.run(
        {"title": "Dup", "description": "I was debited twice $49.00 for order ORD-81094. Duplicate charge TXN-7712 needs refund.", "category": "Refunds", "tenant_id": "tenant-a"},
        driver=MockDriver(),
    )
    res2 = next(e for e in sse2.events if e["event"] == "resolution_end")
    assert res2["data"]["policy_decision"]["decision"] == "require_approval"
    assert before == (_sha(tx), _sha(acc))
