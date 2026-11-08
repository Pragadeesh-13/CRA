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


def test_firebase_config_endpoint(monkeypatch):
    from app.config import settings

    # No project/key is shipped by default; the endpoint must not advertise a
    # stale or placeholder project, and must fail closed.
    for field in (
        "firebase_project_id",
        "firebase_web_api_key",
        "firebase_auth_domain",
        "firebase_storage_bucket",
        "firebase_messaging_sender_id",
        "firebase_app_id",
        "firebase_measurement_id",
    ):
        monkeypatch.setattr(settings, field, "")
    res = client.get("/api/auth/firebase-config")
    assert res.status_code == 503, res.text
    assert res.json()["configured"] is False
    for leaked in ("llm-council-4da2d", "AIza", "projectId", "apiKey", "authDomain"):
        assert leaked not in res.text


def test_firebase_config_endpoint_configured(monkeypatch):
    from app.config import settings

    # Full operator-supplied public web config is surfaced verbatim.
    config = {
        "firebase_project_id": "user-proj-123",
        "firebase_web_api_key": "user-key-abc",
        "firebase_auth_domain": "user-proj-123.firebaseapp.com",
        "firebase_storage_bucket": "user-proj-123.appspot.com",
        "firebase_messaging_sender_id": "111222333",
        "firebase_app_id": "1:111222333:web:abcdef",
        "firebase_measurement_id": "G-ABCDEF",
    }
    for field, value in config.items():
        monkeypatch.setattr(settings, field, value)
    res = client.get("/api/auth/firebase-config")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["configured"] is True
    assert data["projectId"] == "user-proj-123"
    assert data["apiKey"] == "user-key-abc"
    assert data["authDomain"] == "user-proj-123.firebaseapp.com"
    assert data["storageBucket"] == "user-proj-123.appspot.com"
    assert data["messagingSenderId"] == "111222333"
    assert data["appId"] == "1:111222333:web:abcdef"
    assert data["measurementId"] == "G-ABCDEF"


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


def test_auth_google_body_profile_not_trusted(monkeypatch):
    """Admin-Configured fork: /api/auth/google registers ONLY the verifier's
    server-decoded claims; body profile/tenant fields are ignored."""
    import app.main as main

    class _Verifier:
        def verify(self, id_token):
            assert id_token == "google_test_token_123"
            return {
                "id": "firebase-uid-1",
                "uid": "firebase-uid-1",
                "email": "verified@corp.com",
                "fullName": "Verified User",
                "username": "verified",
                "tenant_id": "tenant-a",
            }

    monkeypatch.setattr(main, "firebase_verifier", _Verifier())
    res = client.post(
        "/api/auth/google",
        json={
            "idToken": "google_test_token_123",
            "email": "alice@gmail.com",
            "displayName": "Alice Google",
            "uid": "google_uid_123",
            "photoURL": "https://lh3.googleusercontent.com/a/test-avatar",
            "tenant_id": "tenant-evil",
            "org_id": "tenant-evil",
        },
    )
    assert res.status_code == 200, res.text
    user = res.json()["user"]
    assert user["email"] == "verified@corp.com"
    assert user["tenant_id"] == "tenant-a"
    assert "alice@gmail.com" not in res.text
    assert "test-avatar" not in res.text
    assert "tenant-evil" not in res.text


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
    """Admin-Configured fork: /api/resolve/action is intentionally fail-closed.

    The standalone automation fork expects 200/Refunded here; this variant
    must deny with no success claim and no CSV mutation (live writes out
    of scope, tenant policy not bypassable via this route).
    """
    from app.database import DATABASE_FILE, CUSTOMER_ACCOUNTS_FILE

    before_tx = DATABASE_FILE.read_bytes() if DATABASE_FILE.exists() else None
    before_acc = CUSTOMER_ACCOUNTS_FILE.read_bytes() if CUSTOMER_ACCOUNTS_FILE.exists() else None
    try:
        for payload in (
            {
                "action": "agent_resolve",
                "customer_id": "USR-10492",
                "query": "Order #88219 charged twice",
                "action_details": "Refund Initiated ($49.00 via Stripe API)",
                "notes": "Automated test resolution by Troy",
            },
            {
                "action": "manual_resolve",
                "customer_id": "USR-08914",
                "notes": "Escalated for human operator review in automated test",
            },
        ):
            res = client.post("/api/resolve/action", json=payload)
            assert res.status_code == 403, res.text
            data = res.json()
            assert data["ok"] is False
            assert data["decision"] == "deny"
            assert data["mutated_customer_data"] is False
            assert data["mutated_external"] is False
            assert "refund_id" not in data
            assert "customer" not in data
        # No mutation of either CSV
        assert DATABASE_FILE.read_bytes() == before_tx
        assert CUSTOMER_ACCOUNTS_FILE.read_bytes() == before_acc
    finally:
        if before_tx is not None:
            DATABASE_FILE.write_bytes(before_tx)
        if before_acc is not None:
            CUSTOMER_ACCOUNTS_FILE.write_bytes(before_acc)



