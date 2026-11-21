from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_endpoint():
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["status"] == "healthy"
    assert data["version"] == "3.0.0"


def test_config_endpoint():
    res = client.get("/api/config", headers={"Authorization": "Bearer test_token"})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "provider" in data
    assert "model" in data


def test_models_endpoint():
    res = client.get("/api/models?provider=openrouter", headers={"Authorization": "Bearer test_token"})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert isinstance(data["models"], list)


def test_firebase_config_endpoint():
    res = client.get("/api/auth/firebase-config")
    assert res.status_code == 200
    data = res.json()
    assert "apiKey" in data
    assert "projectId" in data


def test_demo_auth_does_not_create_an_account_or_accept_forged_identity(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth_mode", "optional")
    guest = TestClient(app)
    assert guest.post("/api/auth/signup", json={"fullName": "Test"}).status_code == 400
    signup = guest.post("/api/auth/signup", json={
        "fullName": "Alice Smith", "email": "alice@example.com",
        "password": "secretpassword", "confirmPassword": "secretpassword",
    })
    assert signup.status_code == 403
    login = guest.post("/api/auth/login")
    assert login.json()["mode"] == "guest"
    assert "user" not in login.json()
    assert guest.get("/api/auth/me", headers={"Authorization": "Bearer token_alice"}).status_code == 401


def _firebase_token(subject, project="test-project", sign_in_provider="password"):
    import base64
    import json
    import time

    claims = base64.urlsafe_b64encode(json.dumps({
        "aud": project, "iss": f"https://securetoken.google.com/{project}",
        "sub": subject, "exp": int(time.time()) + 300,
        "firebase": {"sign_in_provider": sign_in_provider},
    }).encode()).decode().rstrip("=")
    # Offline tests mock Identity Toolkit's signature and account verification.
    return f"mock.{claims}.signature"


def test_required_signup_and_password_authentication(monkeypatch):
    from app.auth import firebase
    from app.config import settings

    monkeypatch.setattr(settings, "auth_mode", "required")
    monkeypatch.setattr(settings, "firebase_project_id", "test-project")
    monkeypatch.setattr(settings, "firebase_web_api_key", "mock-key")
    token = _firebase_token("alice-id")
    client = TestClient(app, base_url="https://testserver")
    calls = []
    account = {"localId": "alice-id", "email": "alice@example.com", "emailVerified": False}

    async def provider(operation, body):
        calls.append(operation)
        if operation in {"signUp", "signInWithPassword"}:
            if body["password"] != "secretpassword":
                raise firebase.IdentityError(401, "The identity provider rejected the credentials.")
            return {"idToken": token}
        if operation == "lookup":
            assert body == {"idToken": token}
            return {"users": [account]}
        return {}

    monkeypatch.setattr("app.main.identity_request", provider)
    monkeypatch.setattr(firebase, "identity_request", provider)
    credentials = {"email": "alice@example.com", "password": "secretpassword"}
    created = client.post("/api/auth/signup", json={
        **credentials, "fullName": "Alice", "confirmPassword": "secretpassword",
    })
    assert created.status_code == 201
    assert calls == ["signUp", "update", "sendOobCode"]
    assert "set-cookie" not in created.headers
    assert client.post("/api/auth/login", json={**credentials, "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json=credentials).status_code == 401
    account["emailVerified"] = True
    accepted = client.post("/api/auth/login", json=credentials)
    assert accepted.status_code == 200
    cookie = accepted.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert token not in cookie
    assert client.get("/api/auth/me").json()["user"]["id"] == "firebase:test-project:alice-id"
    client.get("/logout", follow_redirects=False)
    assert client.get("/api/auth/me").status_code == 401


def test_required_google_uses_verified_profile_and_isolates_keys(monkeypatch):
    from app.auth import firebase
    from app.config import settings

    monkeypatch.setattr(settings, "auth_mode", "production")
    monkeypatch.setattr(settings, "firebase_project_id", "test-project")
    monkeypatch.setattr(settings, "firebase_web_api_key", "mock-key")
    alice_token = _firebase_token("alice", sign_in_provider="google.com")
    bob_token = _firebase_token("bob", sign_in_provider="google.com")

    async def provider(operation, body):
        assert operation == "lookup"
        subject = {alice_token: "alice", bob_token: "bob"}.get(body["idToken"])
        if subject is None:
            raise firebase.IdentityError(401, "The identity provider rejected the credentials.")
        return {"users": [{
            "localId": subject, "email": f"{subject}@example.com", "emailVerified": True,
            "photoUrl": f"https://example.com/{subject}.png",
        }]}

    monkeypatch.setattr(firebase, "identity_request", provider)
    alice = TestClient(app, base_url="https://testserver")
    bob = TestClient(app, base_url="https://testserver")
    anonymous = TestClient(app, base_url="https://testserver")
    forged = anonymous.post("/api/auth/google", json={
        "idToken": _firebase_token("mallory", sign_in_provider="google.com"), "uid": "alice",
    })
    assert forged.status_code == 401 and "set-cookie" not in forged.headers
    wrong_project = anonymous.post("/api/auth/google", json={
        "idToken": _firebase_token("alice", project="other-project", sign_in_provider="google.com"),
    })
    assert wrong_project.status_code == 401
    approved = alice.post("/api/auth/google", json={
        "idToken": alice_token, "uid": "bob", "email": "bob@example.com", "photoURL": "fake",
    })
    assert approved.json()["user"] == {
        "id": "firebase:test-project:alice", "fullName": "alice@example.com",
        "email": "alice@example.com", "photoURL": "https://example.com/alice.png",
    }
    assert alice.post("/api/config", json={
        "provider": "openrouter", "model": "alice-private-model", "apiKey": "test-key",
    }).status_code == 200
    assert bob.post("/api/auth/google", json={"idToken": bob_token}).status_code == 200
    assert alice.get("/api/config").json()["model"] == "alice-private-model"
    assert bob.get("/api/config").json()["model"] != "alice-private-model"
    assert anonymous.get("/api/config", headers={"Authorization": "Bearer alice"}).status_code == 401
    assert anonymous.delete("/api/auth/keys").status_code == 401


def test_static_index_serving():
    res = client.get("/")
    assert res.status_code == 200
    assert "Agentic complaint resolution system" in res.text
    assert "feed-list" in res.text


def test_auth_required_redirect(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth_mode", "required")
    fresh_client = TestClient(app)
    response = fresh_client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login.html"
    assert fresh_client.get("/login.html", follow_redirects=False).status_code == 200
    assert fresh_client.get("/?token=token_alice", follow_redirects=False).status_code == 302
    fresh_client.cookies.set("__session", "token_alice")
    assert fresh_client.get("/", follow_redirects=False).status_code == 302


def test_lmstudio_config_and_driver(monkeypatch):
    monkeypatch.delenv("LMSTUDIO_BASE_URL", raising=False)
    # Configure LM Studio provider with a custom model name and no API key
    res = client.post(
        "/api/config",
        json={"provider": "lmstudio", "model": "qwen2.5-7b-instruct", "apiKey": ""},
        headers={"Authorization": "Bearer test_token"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["provider"] == "lmstudio"
    assert data["model"] == "qwen2.5-7b-instruct"
    assert data["baseUrl"] in ("http://localhost:1234/v1", "http://localhost:1235/v1")

    # Get config should reflect lmstudio without requiring an API key
    res_get = client.get("/api/config", headers={"Authorization": "Bearer test_token"})
    assert res_get.status_code == 200
    cfg = res_get.json()
    assert cfg["provider"] == "lmstudio"
    assert cfg["model"] == "qwen2.5-7b-instruct"
    assert cfg["requiresKey"] is False
    assert cfg["baseUrl"] in ("http://localhost:1234/v1", "http://localhost:1235/v1")

    # Models endpoint for lmstudio should succeed and return curated fallback (empty list)
    res_models = client.get("/api/models?provider=lmstudio", headers={"Authorization": "Bearer test_token"})
    assert res_models.status_code == 200
    m_data = res_models.json()
    assert m_data["ok"] is True
    assert isinstance(m_data["models"], list)


def test_cra_model_endpoints():
    # Test fast category classification
    res_cat = client.post(
        "/api/cra/classify",
        json={"complaint": "I was charged twice for my subscription renewal this month."},
    )
    assert res_cat.status_code == 200
    cat_data = res_cat.json()
    assert cat_data["ok"] is True
    assert "predicted_category" in cat_data
    assert "category_confidence" in cat_data

    # Test full intelligence report
    res_res = client.post(
        "/api/cra/resolve",
        json={"complaint": "Two-factor authentication codes are not being delivered to my phone. Order #9812."},
    )
    assert res_res.status_code == 200
    res_data = res_res.json()
    assert res_data["ok"] is True
    assert "report" in res_data
    assert "recommended_team" in res_data["report"]
    assert "similar_cases" in res_data["report"]
    assert "extracted_entities" in res_data["report"]


def test_triage_clean_fast_execution():
    """Verifies that non-ML triage endpoints respond quickly without heavy ML models."""
    import time
    t0 = time.time()
    res = client.post(
        "/api/cra/classify",
        json={"complaint": "Double billing on order ORD-81094."},
    )
    elapsed = time.time() - t0
    assert res.status_code == 200
    assert elapsed < 0.5, f"Triage took too long: {elapsed:.3f}s"
    data = res.json()
    assert data["ok"] is True
    assert data["predicted_category"] == "Refunds"


def test_customer_verify_endpoint():
    res = client.get("/api/customer/verify?query=88219")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["verified"] is True
    assert data["customer_name"] == "Alex Morgan"
    assert data["customer_id"] == "USR-10492"
    assert data["has_duplicate"] is True
    assert "data/transactions.csv" in data["summary"]


def test_database_customers_endpoint():
    res = client.get("/api/database/customers")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "customers" in data
    assert isinstance(data["customers"], list)
    assert data["count"] >= 1
    # Check customer ledger schema
    first = data["customers"][0]
    assert "customer_id" in first
    assert "customer_name" in first
    assert "subscription_plan" in first
    assert "customer_email" in first


def test_database_transactions_endpoint():
    res = client.get("/api/database/transactions")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "transactions" in data
    assert isinstance(data["transactions"], list)
    assert data["count"] >= 1
    first = data["transactions"][0]
    assert "transaction_id" in first
    assert "billing_cycle" in first
    assert "amount" in first
    assert "status" in first


def test_resolve_action_agent_and_manual():
    from app.database import DATABASE_FILE
    original_content = DATABASE_FILE.read_text(encoding="utf-8") if DATABASE_FILE.exists() else None
    try:
        # 1. Test Agent Resolve (updates customer account in CSV)
        res_agent = client.post(
            "/api/resolve/action",
            json={
                "action": "agent_resolve",
                "customer_id": "USR-10492",
                "query": "Order #88219 charged twice",
                "action_details": "Refund Initiated ($49.00 via Stripe API)",
                "notes": "Automated test resolution by Troy",
            },
        )
        assert res_agent.status_code == 200
        data_agent = res_agent.json()
        assert data_agent["ok"] is True
        assert data_agent["action"] == "agent_resolve"
        assert data_agent["customer"]["account_status"] == "Active - In Good Standing"
        assert "Refunded" in data_agent["customer"]["refund_status"]
        assert data_agent["customer"]["refund_id"] == "REF-88219-AUTO"
        assert "$0.00" in data_agent["customer"]["account_balance"]

        # 2. Test Manual Resolve
        res_manual = client.post(
            "/api/resolve/action",
            json={
                "action": "manual_resolve",
                "customer_id": "USR-08914",
                "notes": "Escalated for human operator review in automated test",
            },
        )
        assert res_manual.status_code == 200
        data_manual = res_manual.json()
        assert data_manual["ok"] is True
        assert data_manual["action"] == "manual_resolve"
        assert data_manual["status"] == "simulated"
        assert data_manual["database_updated"] is False
        assert data_manual["customer"]["customer_notified"] is False
    finally:
        if original_content is not None:
            DATABASE_FILE.write_text(original_content, encoding="utf-8")





def test_config_rejects_malformed_json_without_changing_preferences():
    client = TestClient(app, raise_server_exceptions=False)
    before = client.get("/api/config").json()
    response = client.post("/api/config", content='{"showReasoning":', headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert response.json()["error"] == "Request body must be a JSON object"
    assert client.get("/api/config").json() == before


def test_ticket_intake_rejects_invalid_body_even_with_query_parameter(monkeypatch):
    from app import main
    from app.simulation.session_registry import SessionRegistry

    registry = SessionRegistry(main.settings, main.session_registry.driver)
    monkeypatch.setattr(main, "session_registry", registry)
    started = []

    async def summarise(query, driver):
        started.append(query)

    monkeypatch.setattr(registry.default_session.orchestrator, "summarise_ticket", summarise)
    client = TestClient(app, raise_server_exceptions=False)
    for invalid in ('{"query":', '[]'):
        result = client.post("/api/next", params={"query": "Synthetic query"}, content=invalid,
                             headers={"Content-Type": "application/json"})
        assert result.status_code == 400
        assert result.json()["error"] == "Request body must be a JSON object"
    assert registry.default_session.task is None
    assert not started

    result = client.post("/api/next", params={"query": "Synthetic query"})
    assert result.status_code == 200
    assert result.json()["started"] is True


def test_config_reports_pending_ticket_task_as_busy(monkeypatch):
    from types import SimpleNamespace

    from app import main
    from app.simulation.session_registry import SessionRegistry

    registry = SessionRegistry(main.settings, main.session_registry.driver)
    monkeypatch.setattr(main, "session_registry", registry)
    monkeypatch.setattr(registry.default_session, "task", SimpleNamespace(done=lambda: False))
    client = TestClient(app)
    assert client.get("/api/config").json()["running"] is True
    response = client.post("/api/config", json={"provider": "lmstudio", "showReasoning": True})
    assert response.status_code == 409
    assert registry.default_session.orchestrator.show_reasoning_override is None


def test_ticket_intake_rejects_non_text_queries_before_starting(monkeypatch):
    from app import main
    from app.simulation.session_registry import SessionRegistry

    registry = SessionRegistry(main.settings, main.session_registry.driver)
    monkeypatch.setattr(main, "session_registry", registry)
    started = []

    async def summarise(query, driver):
        started.append(query)

    monkeypatch.setattr(registry.default_session.orchestrator, "summarise_ticket", summarise)
    client = TestClient(app)
    for path in ("/api/next", "/api/ticket/next"):
        for value in (["Duplicate charge"], {"complaint": "Duplicate charge"}, 42, True, False):
            response = client.post(path, params={"query": "Legacy query"}, json={"query": value})
            assert response.status_code == 400
            assert response.json()["error"] == "Complaint ticket details must be text."
            assert registry.default_session.task is None
    assert started == []

    with client:
        response = client.post("/api/ticket/next", params={"query": "Legacy query"},
                               json={"query": "  Duplicate charge  "})
        assert response.status_code == 200
        assert response.json()["started"] is True
    assert started == ["Duplicate charge"]
