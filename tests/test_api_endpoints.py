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


def test_auth_signup_validation():
    # Incomplete body should return 400
    res = client.post("/api/auth/signup", json={"fullName": "Test"})
    assert res.status_code == 400

    # Successful signup
    res_ok = client.post(
        "/api/auth/signup",
        json={
            "fullName": "Alice Smith",
            "email": "alice@example.com",
            "username": "alice",
            "password": "secretpassword",
            "confirmPassword": "secretpassword",
        },
    )
    assert res_ok.status_code == 201


def test_auth_login():
    res = client.post("/api/auth/login", json={"username": "alice", "password": "secretpassword"})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["user"]["username"] == "alice"


def test_auth_google_with_photo():
    res = client.post(
        "/api/auth/google",
        json={
            "idToken": "google_test_token_123",
            "email": "alice@gmail.com",
            "displayName": "Alice Google",
            "uid": "google_uid_123",
            "photoURL": "https://lh3.googleusercontent.com/a/test-avatar",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["user"]["photoURL"] == "https://lh3.googleusercontent.com/a/test-avatar"
    assert data["user"]["email"] == "alice@gmail.com"


def test_static_index_serving():
    res = client.get("/")
    assert res.status_code == 200
    assert "Agentic complaint resolution system" in res.text
    assert "feed-list" in res.text


def test_auth_required_redirect():
    from app.config import settings
    orig_mode = settings.auth_mode
    try:
        settings.auth_mode = "required"
        fresh_client = TestClient(app)
        # Unauthenticated request to / should redirect to /login.html
        res = fresh_client.get("/", follow_redirects=False)
        assert res.status_code == 302
        assert res.headers["location"] == "/login.html"

        # Login page itself should be accessible without redirect loop
        res_login = fresh_client.get("/login.html", follow_redirects=False)
        assert res_login.status_code == 200

        # Authenticated request with cookie should serve index.html
        res_auth = fresh_client.get("/", cookies={"__session": "token_alice"}, follow_redirects=False)
        assert res_auth.status_code == 200
        assert "Agentic complaint resolution system" in res_auth.text
    finally:
        settings.auth_mode = orig_mode


def test_lmstudio_config_and_driver():
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
    assert data["baseUrl"] == "http://localhost:1235/v1"

    # Get config should reflect lmstudio without requiring an API key
    res_get = client.get("/api/config", headers={"Authorization": "Bearer test_token"})
    assert res_get.status_code == 200
    cfg = res_get.json()
    assert cfg["provider"] == "lmstudio"
    assert cfg["model"] == "qwen2.5-7b-instruct"
    assert cfg["requiresKey"] is False
    assert cfg["baseUrl"] == "http://localhost:1235/v1"

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
    # Check customer & transaction ledger schema
    first = data["customers"][0]
    assert "customer_id" in first
    assert "customer_name" in first
    assert "subscription_plan" in first
    assert "transaction_id" in first


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
        assert "Escalated" in data_manual["customer"]["account_status"]
    finally:
        if original_content is not None:
            DATABASE_FILE.write_text(original_content, encoding="utf-8")



