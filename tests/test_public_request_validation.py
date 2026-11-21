import pytest
from fastapi.testclient import TestClient

from app import main, triage


@pytest.fixture
def isolated_http(monkeypatch):
    calls = []

    async def identity(operation, body):
        calls.append((operation, body))
        return {"idToken": "synthetic-token"}

    async def verify(token, **kwargs):
        calls.append(("verify", token))
        return {"id": "validation-fixture"}

    async def triage_report(text):
        calls.append(("triage", text))
        return {"complaint": text}

    async def classify_report(text):
        calls.append(("classify", text))
        return {"predicted_category": "Billing"}

    def resolve_action(**kwargs):
        calls.append(("action", kwargs))
        return {"ok": True}

    monkeypatch.setattr(main, "guard", main.SessionGuard(mode="optional"))
    monkeypatch.setattr(main, "identity_request", identity)
    monkeypatch.setattr(main, "verified_user", verify)
    monkeypatch.setattr(main, "execute_agent_resolution", resolve_action)
    monkeypatch.setattr(main, "execute_manual_resolution", resolve_action)
    monkeypatch.setattr(triage, "predict_category", lambda _: "Billing")
    monkeypatch.setattr(triage, "async_triage_complaint", triage_report)
    monkeypatch.setattr(triage, "async_predict_category", classify_report)
    return TestClient(main.app, raise_server_exceptions=False), calls


@pytest.mark.parametrize("path", [
    "/api/cra/triage", "/api/cra/resolve", "/api/cra/classify", "/api/cra/predict",
    "/api/resolve/action", "/api/round/begin", "/api/auth/signup", "/api/auth/login", "/api/auth/google",
])
@pytest.mark.parametrize("body", ['{"bad":', '[]', 'null', '"text"', '{"unused": NaN}'])
def test_rejects_invalid_json_objects_before_dispatch(isolated_http, path, body):
    http, calls = isolated_http
    result = http.post(path, content=body, headers={"Content-Type": "application/json"})
    assert result.status_code == 400
    assert result.json() == {"ok": False, "error": "Request body must be a JSON object"}
    assert not calls


@pytest.mark.parametrize(("path", "body"), [
    ("/api/cra/triage", {"complaint": ["invoice"]}),
    ("/api/cra/classify", {"complaint": True}),
    ("/api/cra/resolve", {"query": 10}),
    ("/api/cra/predict", {"text": {"query": "invoice"}}),
    ("/api/resolve/action", {}),
    ("/api/resolve/action", {"action": [], "query": "Example"}),
    ("/api/resolve/action", {"query": ["invoice"]}),
    ("/api/resolve/action", {"customer_id": {}, "query": "Example"}),
    ("/api/resolve/action", {"category": [], "query": "Example"}),
    ("/api/resolve/action", {"action_details": {}, "query": "Example"}),
    ("/api/resolve/action", {"notes": False, "query": "Example"}),
    ("/api/round/begin", {}),
    ("/api/round/begin", {"round": True}),
    ("/api/round/begin", {"round": 2.5}),
    ("/api/round/begin", {"round": "2.00000000000000001"}),
    ("/api/round/begin", {"round": "NaN"}),
    ("/api/auth/signup", {"fullName": ["Name"], "email": "user@example.test", "password": "example", "confirmPassword": "example"}),
    ("/api/auth/signup", {"fullName": "Name", "email": {"value": "user@example.test"}, "password": "example", "confirmPassword": "example"}),
    ("/api/auth/signup", {"fullName": "Name", "email": "user@example.test", "password": 123456, "confirmPassword": "123456"}),
    ("/api/auth/google", {"idToken": []}),
])
def test_rejects_invalid_fields_before_dispatch(isolated_http, path, body):
    http, calls = isolated_http
    result = http.post(path, json=body)
    assert result.status_code == 400
    assert result.json()["ok"] is False
    assert not calls


def test_valid_aliases_and_bodyless_guest_login(isolated_http):
    http, calls = isolated_http
    assert http.post("/api/cra/resolve", json={"complaint": "", "query": " invoice "}).json()["report"] == {"complaint": "invoice"}
    assert http.post("/api/cra/predict", json={"text": " invoice "}).json()["predicted_category"] == "Billing"
    assert http.post("/api/resolve/action", json={"ticket_id": "USR-10", "notes": None}).status_code == 200
    assert calls[-1][1]["customer_id"] == "USR-10"
    assert http.post("/api/resolve/action", json={"ticket_id": 123}).status_code == 200
    assert calls[-1][1]["customer_id"] == "123"
    assert http.post("/api/resolve/action", json={"category": "Billing"}).status_code == 200
    for round_id in (0, 2.0, "3", "4.0"):
        out = http.post("/api/round/begin", json={"round": round_id})
        assert out.json() == {"ok": True, "round": int(float(round_id))}
    assert http.post("/api/auth/login").json()["mode"] == "guest"
    assert http.post("/api/auth/login", json={}).json()["mode"] == "guest"


def test_round_rejects_fraction_below_float_precision(isolated_http):
    http, _ = isolated_http
    response = http.post("/api/round/begin", content='{"round":2.00000000000000001}')
    assert response.status_code == 400
    assert response.json()["error"] == "Invalid round number"


def test_round_rejects_unrepresentable_exponent(isolated_http):
    http, _ = isolated_http
    response = http.post("/api/round/begin", content='{"round":1e99999999999999999999}')
    assert response.status_code == 400


def test_required_login_does_not_coerce_email_and_keeps_username_alias(isolated_http, monkeypatch):
    http, calls = isolated_http
    monkeypatch.setattr(main.guard, "_mode", "required")
    for body in ("[", "[]", "null", ""):
        assert http.post("/api/auth/login", content=body).status_code == 400
    bad_email = http.post("/api/auth/login", json={"email": ["user@example.test"], "password": "password"})
    assert bad_email.status_code == 400
    bad_alias = http.post("/api/auth/login", json={"email": [], "username": "user@example.test", "password": "password"})
    assert bad_alias.status_code == 400
    assert not calls

    accepted = http.post("/api/auth/login", json={"username": "user@example.test", "password": "password"})
    assert accepted.status_code == 200
    assert calls[0][0] == "signInWithPassword"
    assert calls[0][1]["email"] == "user@example.test"
