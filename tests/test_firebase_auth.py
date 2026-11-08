"""Firebase ID-token verification tests (Admin-Configured fork).

Uses a mocked verifier for endpoint tests and pure/mocked-SDK tests for the
verifier itself, so no real Firebase credentials or firebase-admin install are
required and no tracked CSV is touched.
"""

from typing import Any, Dict, Optional

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.auth.firebase_verifier import FirebaseTokenVerifier, TokenVerificationError
from app.main import app


client = TestClient(app)


class _FakeVerifier:
    """Stands in for FirebaseTokenVerifier at the /api/auth/google boundary."""

    def __init__(self, principal: Optional[Dict[str, Any]] = None, error: Optional[Exception] = None):
        self.principal = principal
        self.error = error
        self.calls = []

    def verify(self, id_token: str) -> Optional[Dict[str, Any]]:
        self.calls.append(id_token)
        if self.error is not None:
            raise self.error
        return dict(self.principal) if self.principal else None


@pytest.fixture
def install_verifier(monkeypatch):
    def _install(fake: _FakeVerifier) -> _FakeVerifier:
        monkeypatch.setattr(main, "firebase_verifier", fake)
        return fake

    return _install


# ── Verifier internals (no SDK required) ────────────────────────────────────

def test_principal_derives_only_from_decoded_claims():
    v = FirebaseTokenVerifier(project_id="proj")
    user = v._principal_from_claims(
        {"uid": "uid-1", "email": "real@corp.com", "name": "Real Name", "tenant_id": "tenant-a", "role": "admin"}
    )
    assert user["id"] == "uid-1" and user["email"] == "real@corp.com"
    assert user["fullName"] == "Real Name" and user["tenant_id"] == "tenant-a"
    assert "role" not in user  # unverified/extra claims are not trusted as identity

    assert "tenant_id" not in v._principal_from_claims({"uid": "u2", "tenant_id": "Bad Slug"})
    assert "tenant_id" not in v._principal_from_claims({"sub": "u3", "org_id": "org-01"})
    assert "tenant_id" not in v._principal_from_claims({"uid": "u4"})
    with pytest.raises(TokenVerificationError):
        v._principal_from_claims({})


def test_principal_org_id_only_does_not_authorize_tenant():
    """Tenancy is canonicalized on tenant_id only; org_id never authorizes."""
    v = FirebaseTokenVerifier(project_id="proj")
    user = v._principal_from_claims(
        {"uid": "u-org", "email": "o@corp.com", "org_id": "tenant-b", "role": "admin"}
    )
    assert user["id"] == "u-org"
    assert "tenant_id" not in user


def test_verify_generic_error_when_sdk_unavailable(monkeypatch):
    v = FirebaseTokenVerifier(project_id="proj")

    def _boom():
        raise ImportError("no module named firebase_admin at /secret/path")

    monkeypatch.setattr(v, "_load_firebase_admin", _boom)
    with pytest.raises(TokenVerificationError) as exc:
        v.verify("tok")
    assert exc.value.status == 503 and exc.value.error_type == "verifier_unavailable"
    assert "secret" not in exc.value.message.lower()
    assert "/secret" not in str(exc.value)


def test_verify_generic_error_on_credential_failure(monkeypatch):
    v = FirebaseTokenVerifier(project_id="proj")
    v.service_account = "not-a-real-path.json"  # malformed inline JSON

    class _Creds:
        def ApplicationDefault(self):
            return object()

        def Certificate(self, arg):
            return object()

    monkeypatch.setattr(v, "_load_firebase_admin", lambda: (object(), object(), _Creds()))
    with pytest.raises(TokenVerificationError) as exc:
        v.verify("tok")
    assert exc.value.status == 503 and exc.value.error_type == "verifier_unavailable"
    assert exc.value.message == "invalid FIREBASE_SERVICE_ACCOUNT"


def test_verify_missing_project_id_fails_closed_503(monkeypatch):
    """No default project is shipped: an empty project id must fail closed."""
    v = FirebaseTokenVerifier(project_id="")
    monkeypatch.setattr(
        v, "_load_firebase_admin", lambda: (_FakeAdmin(), _RecordingAuth(decoded={"uid": "u"}), _Creds())
    )
    with pytest.raises(TokenVerificationError) as exc:
        v.verify("tok")
    assert exc.value.status == 503 and exc.value.error_type == "verifier_unavailable"
    assert exc.value.message == "Firebase project ID is not configured"


# ── Verifier with mocked SDK ────────────────────────────────────────────────

class _RecordingAuth:
    def __init__(self, decoded=None, error=None):
        self.decoded = decoded
        self.error = error
        self.kwargs = None

    # Explicit signature so the verifier can pass app/check_revoked.
    def verify_id_token(self, id_token, app=None, check_revoked=False):
        self.kwargs = {"app": app, "check_revoked": check_revoked}
        if self.error is not None:
            raise self.error
        return self.decoded


class _Creds:
    def ApplicationDefault(self):
        return object()

    def Certificate(self, arg):
        return object()


class _FakeAdmin:
    def initialize_app(self, cred, options=None):
        return "app-sentinel"

    def get_app(self):
        return "app-sentinel"


def _verifier_with(monkeypatch, auth_obj) -> FirebaseTokenVerifier:
    v = FirebaseTokenVerifier(project_id="proj-123")
    monkeypatch.setattr(v, "_load_firebase_admin", lambda: (_FakeAdmin(), auth_obj, _Creds()))
    return v


def test_verify_success_returns_principal_and_passes_app_and_revocation(monkeypatch):
    auth_obj = _RecordingAuth(decoded={"uid": "uid-1", "email": "v@corp.com", "tenant_id": "tenant-a"})
    v = _verifier_with(monkeypatch, auth_obj)
    user = v.verify("tok-ok")
    assert user["id"] == "uid-1" and user["tenant_id"] == "tenant-a"
    assert auth_obj.kwargs["app"] is v._app and v._app == "app-sentinel"
    assert auth_obj.kwargs["check_revoked"] is True


def test_verify_invalid_token_generic_error_no_leak(monkeypatch):
    auth_obj = _RecordingAuth(error=Exception("audience mismatch for project secret-proj with key sk-live-XXXX"))
    v = _verifier_with(monkeypatch, auth_obj)
    with pytest.raises(TokenVerificationError) as exc:
        v.verify("tok-bad")
    assert exc.value.status == 401 and exc.value.error_type == "invalid_token"
    assert exc.value.message == "Firebase ID token verification failed"
    assert "secret-proj" not in str(exc.value) and "sk-live" not in str(exc.value)
    # app/revocation still enforced before failure
    assert auth_obj.kwargs["app"] is v._app and auth_obj.kwargs["check_revoked"] is True


# ── /api/auth/google endpoint (verifier mocked) ─────────────────────────────

def test_google_verified_claim_propagates_and_forged_body_ignored(install_verifier):
    fake = install_verifier(
        _FakeVerifier(
            principal={
                "id": "firebase-uid-1",
                "uid": "firebase-uid-1",
                "email": "real@corp.com",
                "fullName": "Real Name",
                "username": "real",
                "tenant_id": "tenant-a",
            }
        )
    )
    r = client.post(
        "/api/auth/google",
        json={
            "idToken": "tok-verified",
            "uid": "attacker-uid",
            "email": "attacker@evil.com",
            "displayName": "Attacker",
            "tenant_id": "tenant-evil",
            "org_id": "tenant-evil",
            "photoURL": "https://evil.example/a.png",
        },
    )
    assert r.status_code == 200, r.text
    user = r.json()["user"]
    assert user["id"] == "firebase-uid-1" and user["email"] == "real@corp.com"
    assert user["tenant_id"] == "tenant-a"
    assert "attacker" not in r.text and "tenant-evil" not in r.text and "evil.example" not in r.text
    assert fake.calls == ["tok-verified"]

    # Registered principal (server-originated) is what /api/auth/me returns.
    me = client.get("/api/auth/me", headers={"Authorization": "Bearer tok-verified"})
    assert me.status_code == 200
    assert me.json()["user"]["tenant_id"] == "tenant-a"


def test_google_invalid_token_rejected(install_verifier):
    install_verifier(
        _FakeVerifier(error=TokenVerificationError("invalid_token", "Firebase ID token verification failed", 401))
    )
    r = client.post("/api/auth/google", json={"idToken": "tok-bad", "tenant_id": "tenant-evil"})
    assert r.status_code == 401, r.text
    body = r.json()
    assert body["ok"] is False and body["error_type"] == "invalid_token"
    assert "tenant-evil" not in r.text


def test_google_verifier_unavailable_fails_closed(install_verifier):
    install_verifier(
        _FakeVerifier(error=TokenVerificationError("verifier_unavailable", "firebase-admin SDK unavailable", 503))
    )
    r = client.post("/api/auth/google", json={"idToken": "tok"})
    assert r.status_code == 503, r.text
    assert r.json()["error_type"] == "verifier_unavailable"


def test_google_missing_token_400(install_verifier):
    install_verifier(_FakeVerifier(principal={"id": "x"}))
    r = client.post("/api/auth/google", json={})
    assert r.status_code == 400


# ── Principal mode behavior ─────────────────────────────────────────────────

def test_required_mode_unknown_bearer_returns_401(monkeypatch):
    monkeypatch.setattr(main.guard, "_mode", "required")
    fresh = TestClient(app)  # no persisted cookies from other tests
    r = fresh.get("/api/policy", headers={"Authorization": "Bearer totally-unknown-token"})
    assert r.status_code == 401, r.text
    r2 = fresh.get("/api/policy")
    assert r2.status_code == 401, r2.text


def test_optional_mode_unknown_bearer_is_guest_without_tenant(monkeypatch):
    monkeypatch.setattr(main.guard, "_mode", "optional")
    fresh = TestClient(app)
    r = fresh.get("/api/auth/me", headers={"Authorization": "Bearer dev-token-unknown"})
    assert r.status_code == 200, r.text
    user = r.json()["user"]
    assert "tenant_id" not in user and "org_id" not in user


def test_local_login_session_carries_no_tenant_claim():
    r = client.post("/api/auth/login", json={"username": "localdev", "password": "secretpassword"})
    assert r.status_code == 200
    user = r.json()["user"]
    assert "tenant_id" not in user and "org_id" not in user
    me = client.get("/api/auth/me", cookies={"__session": "token_localdev"})
    assert me.status_code == 200
    assert "tenant_id" not in me.json()["user"]
