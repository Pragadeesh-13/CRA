"""
Tests for Troy's autonomous action decision.

Troy (the running LLM / resolution engine) decides whether a ticket needs
a database mutation or human routing (`action_required`), and the UI
renders action/routing buttons *only* when Troy signalled
`action_required === True`:

  - Direct responses (inquiries, how-tos, policy questions) -> no buttons.
  - Database remediation (duplicate charges, billing disputes) -> buttons.

Covers the intent evaluator, /api/customer/verify payloads, the
cra_report / resolution_end SSE payloads from the orchestrator, and the
frontend gating logic in public/js/app.js.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.providers.mock_driver import MockDriver
from app.simulation.troy import ComplaintResolutionOrchestrator

client = TestClient(app)

INQUIRY = "How do I invite teammates?"
DOCS_INQUIRY = "Where is the API documentation?"
DUPLICATE = "Charged twice for order ORD-88219"


# ---------------------------------------------------------------------------
# Intent evaluator (the decision primitive)
# ---------------------------------------------------------------------------


class TestIntentDecision:
    @pytest.mark.parametrize("text", [INQUIRY, DOCS_INQUIRY, "How do I reset my password?", "What is your refund policy?"])
    def test_inquiries_are_direct_responses(self, text):
        intent = ComplaintResolutionOrchestrator.evaluate_complaint_intent(text)
        assert intent["intent"] == "direct_response"
        assert intent["requires_db_mutation"] is False

    @pytest.mark.parametrize(
        "text",
        [DUPLICATE, "I was charged twice for ORD-88219", "Duplicate charge of $49 for ORD-88219", "Please refund my duplicate debit"],
    )
    def test_duplicate_charges_require_action(self, text):
        intent = ComplaintResolutionOrchestrator.evaluate_complaint_intent(text)
        assert intent["intent"] == "database_remediation"
        assert intent["requires_db_mutation"] is True

    def test_refunds_category_forces_action(self):
        intent = ComplaintResolutionOrchestrator.evaluate_complaint_intent("hello", category="Refunds")
        assert intent["requires_db_mutation"] is True


# ---------------------------------------------------------------------------
# /api/customer/verify carries Troy's decision
# ---------------------------------------------------------------------------


class TestVerifyEndpointDecision:
    def test_inquiry_has_no_action_required(self):
        res = client.get("/api/customer/verify", params={"query": INQUIRY})
        assert res.status_code == 200
        data = res.json()
        assert data["action_required"] is False
        assert data["requires_action"] is False
        assert data["intent"]["intent"] == "direct_response"
        assert data["intent"]["requires_db_mutation"] is False

    def test_docs_inquiry_has_no_action_required(self):
        res = client.get("/api/customer/verify", params={"query": DOCS_INQUIRY})
        assert res.status_code == 200
        data = res.json()
        assert data["action_required"] is False
        assert data["intent"]["intent"] == "direct_response"

    def test_duplicate_charge_requires_action(self):
        res = client.get("/api/customer/verify", params={"query": DUPLICATE})
        assert res.status_code == 200
        data = res.json()
        assert data["action_required"] is True
        assert data["requires_action"] is True
        assert data["intent"]["intent"] == "database_remediation"
        assert data["intent"]["requires_db_mutation"] is True


# ---------------------------------------------------------------------------
# Orchestrator SSE payloads carry Troy's decision
# ---------------------------------------------------------------------------


class _RecordingSse:
    def __init__(self):
        self.events = []

    def broadcast(self, event, data):
        self.events.append((event, data))

    def payloads(self, event):
        return [data for name, data in self.events if name == event]


def _fake_report(category: str, team: str):
    return {
        "predicted_category": category,
        "category_confidence": 0.95,
        "top_3_categories": [{"category": category, "score": 0.95}],
        "recommended_team": team,
        "team_method": "Local LLM",
        "sla": {"estimated_hours": 4.0, "sla_risk": "Low"},
        "extracted_entities": {"references": [], "dates": [], "amounts": [], "products": []},
        "similar_cases": [],
    }


async def _fake_execute_agent_with_reasoning(**kwargs):
    return {"message": "Section body from Troy."}


async def _run_pipeline(monkeypatch, complaint: str, category: str, team: str, isolated_database):
    import app.triage
    import app.simulation.troy as troy_module

    monkeypatch.setattr(app.triage, "triage_complaint", lambda text: _fake_report(category, team))
    monkeypatch.setattr(troy_module, "execute_agent_with_reasoning", _fake_execute_agent_with_reasoning)

    sse = _RecordingSse()
    orchestrator = ComplaintResolutionOrchestrator(
        config=SimpleNamespace(llm_stream=False, show_reasoning=False),
        driver=MockDriver(),
        sse_manager=sse,
    )
    await orchestrator._run_pipeline({"title": "Ticket", "description": complaint, "url": ""})
    return sse


class TestOrchestratorSseDecision:
    @pytest.mark.asyncio
    async def test_cra_report_and_resolution_end_for_inquiry(self, monkeypatch, isolated_database):
        sse = await _run_pipeline(monkeypatch, INQUIRY, "Onboarding & How-To", "Tier 1 Support", isolated_database)
        cra_reports = sse.payloads("cra_report")
        assert cra_reports, "cra_report was never broadcast"
        assert cra_reports[0]["action_required"] is False
        assert cra_reports[0]["requires_action"] is False
        assert cra_reports[0]["intent"]["intent"] == "direct_response"

        ends = sse.payloads("resolution_end")
        assert ends, "resolution_end was never broadcast"
        assert ends[0]["action_required"] is False
        assert ends[0]["requires_action"] is False
        assert ends[0]["intent"]["requires_db_mutation"] is False
        assert ends[0]["cra_report"]["action_required"] is False

    @pytest.mark.asyncio
    async def test_cra_report_and_resolution_end_for_duplicate_charge(self, monkeypatch, isolated_database):
        sse = await _run_pipeline(monkeypatch, DUPLICATE, "Refunds", "Billing", isolated_database)
        cra_reports = sse.payloads("cra_report")
        assert cra_reports[0]["action_required"] is True
        assert cra_reports[0]["intent"]["intent"] == "database_remediation"

        ends = sse.payloads("resolution_end")
        assert ends[0]["action_required"] is True
        assert ends[0]["requires_action"] is True
        assert ends[0]["intent"]["requires_db_mutation"] is True


# ---------------------------------------------------------------------------
# Frontend: buttons render only when Troy decided action is required
# ---------------------------------------------------------------------------


def _app_js() -> str:
    repo_root = Path(__file__).resolve().parent.parent
    return (repo_root / "public" / "js" / "app.js").read_text(encoding="utf-8")


class TestFrontendActionGate:
    def test_decision_helpers_read_troy_signals(self):
        js = _app_js()
        assert "troyActionDecision" in js
        assert "verifyActionDecision" in js
        for signal in ("action_required", "requires_action", "requires_db_mutation", "database_remediation", "direct_response"):
            assert signal in js, f"frontend does not consult Troy signal: {signal}"
        assert "cachedResolutionEnd" in js

    def test_gate_runs_before_buttons_render(self):
        js = _app_js()
        fn = js[js.index("const renderResolutionActionButtons"): js.index("const finishTransformedStream")]
        gate = fn.index("troyActionDecision()")
        verify_gate = fn.index("verifyActionDecision(verifyData)")
        first_button = fn.index("btn-resolve-manually")
        assert gate < fn.index("/api/customer/verify"), "Troy decision must be checked before the verify fetch"
        assert verify_gate < first_button, "verify decision must be checked before buttons render"
        assert "renderNoActionStatus" in fn

    def test_no_action_path_renders_no_buttons(self):
        js = _app_js()
        start = js.index("const renderNoActionStatus")
        end = js.index("const renderResolutionActionButtons")
        status_fn = js[start:end]
        assert "btn-resolve-manually" not in status_fn
        assert "btn-agent-resolve" not in status_fn
        assert "No action required" in status_fn

    def test_buttons_still_exist_for_action_tickets(self):
        js = _app_js()
        assert "btn-resolve-manually" in js
        assert "btn-agent-resolve" in js
