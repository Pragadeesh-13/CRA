"""Exercise public SDK dispatch and evidence checks without network access."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import troy
from app.api.v1.saas_routes import create_saas_router
from test_troy_opensource_tool import DUPLICATE, _seeded_adapter


BUSINESS_CASES = [
    ("Please send invoice INV-20 for USR-10492", "Billing", "Billing",
     "send_invoice", {"invoice_id": "INV-20"}, "Sent"),
    ("Please unlock my account USR-10492", "Authentication & Access", "Tier 1 Support",
     "unlock_account", {}, "Unlocked"),
    ("Please apply a subscription credit to SUB-30 for USR-10492", "Subscriptions", "Billing",
     "apply_subscription_credit", {"subscription_id": "SUB-30", "amount": 25.0}, "Credited"),
    ("Please retry webhook EVT-40 for USR-10492", "Integrations & APIs", "Engineering",
     "retry_webhook", {"event_id": "EVT-40"}, "Retried"),
]


def make_agent(name=None, result=None, policies=None):
    adapter = _seeded_adapter()
    adapter.customers["USR-10492"].update(
        invoice_id="INV-20", subscription_id="SUB-30", subscription_credit_amount="25.00", event_id="EVT-40",
    )
    agent = troy.Troy(adapter=adapter, driver=object(), policies=policies)
    calls = []
    if name:
        def business_tool(**params):
            calls.append(params)
            return result
        agent.register_tool(business_tool, name=name)
    return agent, calls


@pytest.mark.parametrize("complaint,category,team,tool,params,completed", BUSINESS_CASES)
@pytest.mark.parametrize("mode", ["happy", "unavailable", "approval", "dry_run", "disabled"])
def test_business_resolution(complaint, category, team, tool, params, completed, mode):
    policies = {"approval_required": True} if mode == "approval" else None
    agent, calls = make_agent(None if mode == "unavailable" else tool, {"status": completed}, policies)
    result = agent.resolve(complaint, dry_run=mode == "dry_run", auto_execute=mode != "disabled")
    expected = {
        "happy": "resolved", "unavailable": "needs_review", "approval": "pending_approval",
        "dry_run": "dry_run", "disabled": "not_executed",
    }[mode]
    assert (result["category"], result["team"], result["status"]) == (category, team, expected)
    assert calls == ([{"customer_id": "USR-10492", **params}] if mode == "happy" else [])
    assert result["action"] == (None if mode == "unavailable" else tool)
    assert bool(result["report"]["actions_taken"]) is (mode == "happy")
    assert result["refund_amount"] is None
    audit = agent.adapter.audit_trail[-1]
    assert (audit["status"], audit["summary"], audit["next_steps"]) == (
        expected, result["summary"], result["report"]["next_steps"],
    )


@pytest.mark.parametrize("complaint,tool,context", [
    ("Unlock and send invoice INV-20 for account USR-10492", "unlock_account", {}),
    ("Do not unlock my account USR-10492", "unlock_account", {}),
    ("Generate invoice INV-20 for USR-10492", "send_invoice", {}),
    ("Webhook EVT-40 failed for USR-10492", "retry_webhook", {}),
    ("Please send invoice for USR-10492", "send_invoice", {}),
    ("Please send invoice INV-99 for USR-10492", "send_invoice", {}),
    ("Please send invoice INV-20 for USR-10492", "send_invoice", {"customer_id": "USR-99999"}),
    ("Please send invoice INV-20 for order ORD-88219 USR-10492", "send_invoice", {"order_id": "ORD-99999"}),
    ("Please send invoice INV-20 for USR-10492", "send_invoice", {"invoice_id": "INV-99"}),
    ("Please retry webhook EVT-99 for USR-10492", "retry_webhook", {}),
    ("Please apply a $50 subscription credit to SUB-30 for USR-10492", "apply_subscription_credit", {}),
])
def test_business_conflicts_and_unverified_resources(complaint, tool, context):
    agent, calls = make_agent(tool, {"ok": True})
    result = agent.resolve(complaint, context=context)
    assert result["status"] == "needs_review"
    assert result["action_result"] is None
    assert not calls


@pytest.mark.parametrize("changes,complaint,context", [
    ({}, DUPLICATE.replace("TXN-9012", "TXN-9999"), {}),
    ({}, DUPLICATE.replace("ORD-88219", "ORD-99999"), {}),
    ({}, DUPLICATE.replace("USR-10492", "USR-99999"), {}),
    ({}, DUPLICATE, {"customer_id": "USR-99999"}),
    ({}, DUPLICATE, {"order_id": "ORD-99999"}),
    ({"order_id": "ORD-99999"}, "I was charged twice, customer USR-10492", {}),
    ({"billing_cycle": "September 2026"}, DUPLICATE, {}),
    ({"amount": "NaN"}, DUPLICATE, {}),
    ({"amount": "Infinity"}, DUPLICATE, {}),
    ({"amount": "-49.00"}, DUPLICATE, {}),
    ({"amount": "0"}, DUPLICATE, {}),
    ({}, "Do not refund the charge for USR-10492 order ORD-88219", {}),
    ({}, "No refund for USR-10492 order ORD-88219", {}),
])
def test_refund_rejects_missing_mismatched_or_unverified_evidence(changes, complaint, context, monkeypatch):
    agent, _ = make_agent()
    agent.adapter.transactions[1].update(changes)
    calls = []
    monkeypatch.setattr(agent.adapter, "execute_action", lambda *args: calls.append(args))
    result = agent.resolve(complaint, context=context)
    assert result["status"] == "needs_review"
    assert result["action_result"] is None
    assert result["refund_amount"] is None
    assert not calls


def test_exact_transaction_lookup_and_ledger_amount(monkeypatch):
    agent, _ = make_agent()
    agent.adapter.add_transaction({
        "customer_id": "USR-99999", "order_id": "ORD-99999", "transaction_id": "TXN-9999",
        "status": "Settled", "amount": "120.00",
    })
    calls = []
    read = agent.adapter.get_transactions

    def track_reads(customer_id, **filters):
        calls.append((customer_id, filters))
        return read(customer_id, **filters)

    monkeypatch.setattr(agent.adapter, "get_transactions", track_reads)
    info = agent.resolve("How do I export my data?")
    assert info["status"] == "resolved" and info["transactions"] == []
    assert calls == []
    conflict = agent.resolve("Do I have a receipt for USR-10492?", customer_id="USR-99999")
    assert conflict["transactions"] == [] and conflict["customer"] is None
    assert calls == []
    result = agent.resolve("I was charged twice for txn-9012", context={"refund_amount": "1.00"})
    assert calls == [("", {"transaction_id": "TXN-9012"}), ("USR-10492", {})]
    assert result["status"] == "resolved" and result["refund_amount"] == "49.00"
    assert {row["customer_id"] for row in result["transactions"]} == {"USR-10492"}
    assert agent.resolve("I was charged twice for TXN-9012")["status"] == "already_refunded"


def test_credit_limit_and_informational_webhook_question():
    agent, calls = make_agent("apply_subscription_credit", {"status": "Credited"}, {"auto_credit_limit": 10})
    result = agent.resolve(BUSINESS_CASES[2][0])
    assert result["status"] == "pending_approval"
    assert not calls

    agent, calls = make_agent("retry_webhook", {"status": "Retried"})
    result = agent.resolve("How do I retry webhook EVT-40 for USR-10492?", context={"category": "Refunds"})
    assert result["status"] == "resolved" and result["action"] is None
    assert not calls


def test_cli_and_saas_expose_business_outcome(monkeypatch, capsys):
    agent, calls = make_agent("send_invoice", {"status": "Queued"})
    monkeypatch.setattr(troy, "Troy", lambda **kwargs: agent)
    assert troy.main([BUSINESS_CASES[0][0], "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "action_unconfirmed"

    app = FastAPI()
    app.include_router(create_saas_router(agent))
    result = TestClient(app).post("/api/v1/saas/resolve", json={"complaint": BUSINESS_CASES[0][0], "dry_run": True}).json()
    assert result["status"] == "dry_run"
    assert result["report"]["next_steps"]
    assert len(calls) == 1


def test_console_uses_static_category_examples():
    from app.triage import _normalize_llm_report

    report = _normalize_llm_report({
        "predicted_category": "Refunds",
        "similar_cases": [{"ticket_id": "model-invented-ticket"}],
    })
    assert report["case_source"] == "static_category_examples"
    assert all(case["ticket_id"] != "model-invented-ticket" for case in report["similar_cases"])


def test_old_refund_from_another_cycle_does_not_resolve_new_complaint():
    agent, _ = make_agent()
    agent.adapter.transactions[0]["billing_cycle"] = "October 2026"
    agent.adapter.transactions[1].update(status="Refunded", billing_cycle="September 2026")
    result = agent.resolve("I was charged twice for order ORD-88219")
    assert result["status"] == "needs_review"
    assert agent.resolve(DUPLICATE)["status"] == "already_refunded"


def test_equal_renewals_in_distinct_cycles_are_not_a_duplicate():
    agent, _ = make_agent()
    agent.adapter.transactions[0]["billing_cycle"] = "September 2026"
    agent.adapter.transactions[1]["billing_cycle"] = "October 2026"
    result = agent.resolve(DUPLICATE)
    assert result["status"] == "needs_review"
    assert result["action_result"] is None
    assert all(row["status"] == "Settled" for row in agent.adapter.transactions)


def test_ledger_amount_controls_refund_approval():
    agent, _ = make_agent()
    for row in agent.adapter.transactions:
        row["amount"] = "75.00"
    result = agent.resolve(DUPLICATE, context={"refund_amount": "1.00"})
    assert result["status"] == "pending_approval"
    assert result["refund_amount"] == "75.00"
    assert result["action_taken"] is None


@pytest.mark.parametrize("reference", ["transaction TXN-9012", "order ORD-88219"])
def test_in_memory_scoped_resolution_matches_ledger_id_case(reference):
    agent, _ = make_agent()
    for row in agent.adapter.transactions:
        row["transaction_id"] = row["transaction_id"].lower()
        row["order_id"] = row["order_id"].lower()

    result = agent.resolve(f"I was charged twice for {reference}", dry_run=True)

    assert result["status"] == "dry_run"
    assert result["refund_amount"] == "49.00"
    assert result["action"] == "issue_refund"
    assert all(row["status"] == "Settled" for row in agent.adapter.transactions)
