import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from app.config import settings
from app.http.sse_manager import SseManager
from app.providers.mock_driver import MockDriver
from app.simulation.state import SimulationState
from app.simulation.troy import Troy


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class CapturingSse(SseManager):
    def __init__(self):
        super().__init__()
        self.events: List[Dict[str, Any]] = []

    def broadcast(self, event: str, data: Any):
        self.events.append({"event": event, "data": data})
        return super().broadcast(event, data)


def _troy(driver, sse=None):
    return Troy(config=settings, driver=driver, sse_manager=sse or CapturingSse(), state=SimulationState(), enable_barrier=False)


class ForcedDecisionDriver(MockDriver):
    """MockDriver whose intent decision is forced, delegating sections to MockDriver."""
    def __init__(self, forced: str):
        super().__init__()
        self.forced = forced
        self.intent_prompts = []

    async def complete(self, messages, max_tokens=4096, **options):
        text = " ".join(m.get("content", "") for m in (messages or []))
        if "troy_intent_decision" in text:
            self.intent_prompts.append(text)
            if self.forced == "invalid":
                return "not-json-at-all"
            return json.dumps({"decision": self.forced, "rationale": "forced"})
        return await super().complete(messages, max_tokens, **options)


@pytest.mark.asyncio
async def test_configured_driver_decision_controls_routing(isolated_database):
    """A driver forcing direct_response on a duplicate claim must not mutate DB."""
    tx, acc = isolated_database["transactions_file"], isolated_database["accounts_file"]
    before = (_sha(tx), _sha(acc))
    driver = ForcedDecisionDriver("direct_response")
    sse = CapturingSse()
    troy = _troy(driver, sse)
    ticket = {"title": "Dup", "description": "I was charged twice $49.00 for order ORD-81094. Duplicate charge TXN-7712 needs refund.", "category": "Refunds"}
    assert await troy.run(ticket, driver=driver) is True
    assert driver.intent_prompts, "configured driver was not consulted for intent"
    assert before == (_sha(tx), _sha(acc))
    assert "database_updated" not in [e["event"] for e in sse.events]


@pytest.mark.asyncio
async def test_invalid_llm_output_defaults_to_direct(isolated_database):
    tx, acc = isolated_database["transactions_file"], isolated_database["accounts_file"]
    before = (_sha(tx), _sha(acc))
    driver = ForcedDecisionDriver("invalid")
    troy = _troy(driver)
    ticket = {"title": "Dup", "description": "I was charged twice $49.00 for order ORD-81094.", "category": "Refunds"}
    assert await troy.run(ticket, driver=driver) is True
    assert before == (_sha(tx), _sha(acc))


@pytest.mark.asyncio
async def test_offline_mock_intent_and_refund_policy_no_write(isolated_database):
    tx, acc = isolated_database["transactions_file"], isolated_database["accounts_file"]
    driver = MockDriver()
    troy = _troy(driver)
    # Unit: deterministic mock routes policy direct, duplicate to investigation
    d_policy = await troy.decide_intent_via_llm("How do refunds work? What is your refund policy?", category="Refunds", driver=driver)
    d_dup = await troy.decide_intent_via_llm("I was charged twice for ORD-81094", category="Refunds", driver=driver)
    assert d_policy["decision"] == "direct_response"
    assert d_dup["decision"] == "database_investigation"
    # E2E: policy inquiry causes no write
    before = (_sha(tx), _sha(acc), tx.read_bytes(), acc.read_bytes())
    sse = CapturingSse()
    troy2 = _troy(driver, sse)
    ok = await troy2.run({"title": "Refund policy", "description": "How do refunds work? What is your refund policy and timeline?", "category": "Refunds"}, driver=driver)
    assert ok is True
    assert before == (_sha(tx), _sha(acc), tx.read_bytes(), acc.read_bytes())
    assert "database_updated" not in [e["event"] for e in sse.events]


@pytest.mark.asyncio
async def test_missing_evidence_no_write_human_review(isolated_database):
    tx, acc = isolated_database["transactions_file"], isolated_database["accounts_file"]
    before = (_sha(tx), _sha(acc))
    sse = CapturingSse()
    driver = MockDriver()
    troy = _troy(driver, sse)
    ok = await troy.run({"title": "Dup unknown", "description": "I was charged twice $49.00 for order ORD-99999 customer USR-99999.", "category": "Refunds"}, driver=driver)
    assert ok is True
    assert before == (_sha(tx), _sha(acc))
    assert "database_updated" not in [e["event"] for e in sse.events]
    msgs = [str(e["data"].get("message", "")) for e in sse.events if e["event"] == "system"]
    assert any("insufficient" in m.lower() and "human review" in m.lower() for m in msgs)


@pytest.mark.asyncio
async def test_direct_refund_policy_does_not_call_inspection_or_analyze(isolated_database, monkeypatch):
    """Direct refund-policy must not trigger DB inspection/analyze via billing context."""
    import app.database as db

    calls = []

    def _boom(name):
        def _fn(*a, **k):
            calls.append(name)
            raise AssertionError(f"{name} must not be called for direct policy inquiry")
        return _fn

    monkeypatch.setattr(db, "analyze_customer_transactions", _boom("analyze_customer_transactions"))
    monkeypatch.setattr(db, "inspect_account_and_transactions", _boom("inspect_account_and_transactions"))

    driver = MockDriver()
    sse = CapturingSse()
    troy = _troy(driver, sse)
    ok = await troy.run(
        {"title": "Refund policy", "description": "How do refunds work? What is your refund policy and timeline?", "category": "Refunds"},
        driver=driver,
    )
    assert ok is True
    assert calls == []
    assert "database_updated" not in [e["event"] for e in sse.events]
    # Direct policy text: general guidance, no fabricated account claims, asks order ID
    res_end = next((e for e in sse.events if e["event"] == "resolution_end"), None)
    assert res_end is not None
    full = res_end["data"].get("full_resolution", "")
    low = full.lower()
    assert "general" in low and "refund" in low
    assert "order id" in low
    assert "not inspected" in low or "not claim" in low or "no account was inspected" in low
    for banned in ("account records have been reviewed", "remain in good standing", "remains in good standing", "within 30 days", "already refunded", "we have reversed transaction"):
        assert banned not in low, f"fabricated claim present: {banned}"


def test_direct_response_refunds_category_fallback_emits_no_fake_refund():
    """Fallback gated solely on verified is_refund: direct + Refunds must not fabricate a refund."""
    troy = _troy(MockDriver())
    # Policy direct case (verified is_refund=False, neutral context)
    ctx_direct = {"is_refund": False, "reference_id": "POL-1", "txn_analysis": {}}
    out = troy._build_category_fallback(
        pred_cat="Refunds",
        complaint_text="How do refunds work? What is your refund policy?",
        conf_pct=90.0,
        team="Billing",
        sla_hours=4.0,
        report={"team_method": "Rule-based", "sla": {"sla_risk": "Low"}},
        cat_ctx=ctx_direct,
    )
    assert "TXN-9012" not in out and "TXN-9011" not in out and "ORD-88219" not in out
    assert "Alex Morgan" not in out
    assert "ALREADY REFUNDED" not in out and "accidental second debit" not in out.lower()
    assert "Executive Assessment & Category Confirmation" in out
    _low = out.lower()
    assert "order id" in _low and "no account was inspected" in _low
    for _banned in ("account records have been reviewed", "in good standing", "within 30 days"):
        assert _banned not in _low
    # Non-policy direct case also must not use refund branch
    out2 = troy._build_category_fallback(
        pred_cat="Refunds",
        complaint_text="General account question",
        conf_pct=90.0,
        team="Billing",
        sla_hours=4.0,
        report={"team_method": "Rule-based", "sla": {"sla_risk": "Low"}},
        cat_ctx=dict(ctx_direct),
    )
    assert "TXN-9012" not in out2 and "Alex Morgan" not in out2 and "ALREADY REFUNDED" not in out2
    assert "Duplicate charge" not in out2 or "zero database" in out2.lower()


@pytest.mark.asyncio
async def test_missing_positive_amount_no_write(isolated_database):
    """Duplicate candidate with no positive amount must not be remediated (no $49 default)."""
    import csv

    tx = isolated_database["transactions_file"]
    rows = list(csv.DictReader(tx.open()))
    for r in rows:
        if r["transaction_id"] == "TXN-7712":
            r["amount"] = ""
    with tx.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader(); w.writerows(rows)
    before = _sha(tx)
    driver = MockDriver()
    sse = CapturingSse()
    ok = await _troy(driver, sse).run(
        {"title": "Dup", "description": "I was debited twice $49.00 for order ORD-81094. Duplicate charge TXN-7712 needs refund.", "category": "Refunds"},
        driver=driver,
    )
    assert ok is True
    assert _sha(tx) == before
    assert "database_updated" not in [e["event"] for e in sse.events]


@pytest.mark.asyncio
async def test_remediation_failure_no_false_success(isolated_database, monkeypatch):
    """ok=False from remediation must not broadcast database_updated or claim a refund."""
    import app.database as db

    monkeypatch.setattr(db, "remediate_duplicate_charge", lambda **k: {"ok": False, "error": "write failed"})
    tx, acc = isolated_database["transactions_file"], isolated_database["accounts_file"]
    before = (_sha(tx), _sha(acc))
    sse = CapturingSse()
    driver = MockDriver()
    ok = await _troy(driver, sse).run(
        {"title": "Dup", "description": "I was debited twice $49.00 for order ORD-81094. Duplicate charge TXN-7712 needs refund.", "category": "Refunds"},
        driver=driver,
    )
    assert ok is True
    assert before == (_sha(tx), _sha(acc))
    assert "database_updated" not in [e["event"] for e in sse.events]
    msgs = [str(e["data"].get("message", "")) for e in sse.events if e["event"] == "system"]
    assert not any("Status: Refunded" in m for m in msgs)
    assert any("remediation failed" in m.lower() and "human review" in m.lower() for m in msgs)
    res_end = next((e for e in sse.events if e["event"] == "resolution_end"), None)
    assert res_end is not None
    full = res_end["data"].get("full_resolution", "")
    assert "ALREADY REFUNDED" not in full
    assert "We have reversed transaction" not in full
    assert "REF-81094" not in full


@pytest.mark.asyncio
async def test_known_duplicate_remediates_and_is_idempotent(isolated_database):
    tx, acc = isolated_database["transactions_file"], isolated_database["accounts_file"]
    ticket = {"title": "Double debit", "description": "I was debited twice $49.00 for order ORD-81094. Duplicate charge TXN-7712 needs refund.", "category": "Refunds"}
    driver = MockDriver()
    h0 = (_sha(tx), _sha(acc))
    sse1 = CapturingSse()
    assert await _troy(driver, sse1).run(ticket, driver=driver) is True
    h1 = (_sha(tx), _sha(acc))
    assert h0 != h1
    assert "database_updated" in [e["event"] for e in sse1.events]
    # second run: already refunded -> no further mutation
    sse2 = CapturingSse()
    assert await _troy(driver, sse2).run(ticket, driver=driver) is True
    assert h1 == (_sha(tx), _sha(acc))
    msgs = [str(e["data"].get("message", "")) for e in sse2.events if e["event"] == "system"]
    assert any("already refunded" in m.lower() for m in msgs)
