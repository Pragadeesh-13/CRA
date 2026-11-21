import json

import httpx
import pytest
from fastapi.testclient import TestClient

import troy
from app.main import app
from app.providers import jev
from app.triage import ALL_11_CATEGORIES, async_triage_complaint
from test_sdk_resolution_evidence import BUSINESS_CASES, make_agent


@pytest.fixture
def decision_server(monkeypatch):
    monkeypatch.setenv("TROY_JEV_MODEL", "local-decision-model")
    monkeypatch.setenv("TROY_JEV_BASE_URL", "http://localhost:4321/v1/")
    monkeypatch.setenv("TROY_JEV_API_KEY", "test-token")
    original_client = httpx.AsyncClient

    def install(category, *, invalid=False):
        def respond(request):
            assert str(request.url) == "http://localhost:4321/v1/systemone"
            assert request.headers["Authorization"] == "Bearer test-token"
            payload = json.loads(request.content)
            assert payload["model"] == "local-decision-model"
            assert isinstance(payload["state"], str)
            question = payload["questions"]["category"]
            assert question["type"] == "choice"
            choices = question["criteria"]
            assert len(choices) == len(ALL_11_CATEGORIES)
            winner = next(key for key, description in choices.items() if description.startswith(f"{category}:"))
            scores = {key: 0.02 for key in choices}
            scores[winner] = "unscored" if invalid else 0.8
            return httpx.Response(200, json={"answers": {"category": {"choice": winner, "probabilities": scores}}})

        transport = httpx.MockTransport(respond)
        monkeypatch.setattr(jev.httpx, "AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs))

    return install


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ALL_11_CATEGORIES)
async def test_triage_uses_jev_category_and_scores(decision_server, category):
    decision_server(category)
    report = await async_triage_complaint("I need help with order ORD-12345.")
    assert report["predicted_category"] == category
    assert report["category_confidence"] == 0.8
    assert report["team_method"] == "Jev (LM Studio)"
    assert len(report["top_3_categories"]) == 3
    assert report["extracted_entities"]["order_id"] == "ORD-12345"


def test_http_classification_and_cli_use_jev(decision_server, capsys):
    decision_server("Data Sync")
    response = TestClient(app).post("/api/cra/classify", json={"complaint": "Changes never show in my other workspace."})
    assert response.json()["predicted_category"] == "Data Sync"
    assert troy.main(["Changes never show in my other workspace.", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["predicted_category"] == "Data Sync"
    assert result["classification_method"] == "jev"
    assert result["team"] == "Engineering"


@pytest.mark.asyncio
async def test_invalid_decision_falls_back_to_rules(decision_server):
    decision_server("Security", invalid=True)
    report = await async_triage_complaint("The webhook delivery timed out.")
    assert report["predicted_category"] == "Integrations & API"
    assert report["team_method"] == "Deterministic Fallback"


def test_classification_alone_cannot_execute_refund(decision_server, monkeypatch):
    decision_server("Refunds")
    complaint = "My dashboard won't load."
    result = troy.Troy(driver=object()).resolve(complaint, customer_id="USR-10492")
    assert result["predicted_category"] == "Refunds"
    assert not result["intent"]["requires_db_mutation"]
    assert result["action_result"] is None

    import app.main as main
    def unexpected_action(**kwargs):
        pytest.fail("An inferred category must not dispatch a refund")
    monkeypatch.setattr(main, "execute_agent_resolution", unexpected_action)
    response = TestClient(app).post("/api/resolve/action", json={"query": complaint, "customer_id": "USR-10492"})
    assert response.status_code == 400


@pytest.mark.parametrize("complaint,category,team,tool,params,completed", BUSINESS_CASES)
def test_jev_routes_non_refund_actions(decision_server, complaint, category, team, tool, params, completed):
    decision_server(category)
    agent, calls = make_agent(tool, {"status": completed})
    result = agent.resolve(complaint)
    assert result["classification_method"] == "jev"
    assert (result["category"], result["team"], result["status"]) == (category, team, "resolved")
    assert len(calls) == 1
    assert all(calls[0][key] == value for key, value in params.items())
