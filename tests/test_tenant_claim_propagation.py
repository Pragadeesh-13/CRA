"""Trusted tenant-context propagation for the Admin-Configured fork.

Proves that tenant context is taken ONLY from the authenticated server-side
principal (`request.state.session_user`), and that spoofed values supplied via
query params, JSON body, URL title, or complaint text are ignored. No tracked
CSV is touched (the orchestrator is faked, so no DB access occurs).
"""

import threading
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.auth import resolve_trusted_tenant_id
from app.config import settings
from app.http.sse_manager import SseManager
from app.main import app, _story_with_trusted_tenant


client = TestClient(app)


# ── Resolver: trusted principal claim only ──────────────────────────────────

def test_resolver_reads_valid_claim_from_principal():
    assert resolve_trusted_tenant_id({"tenant_id": "tenant-a"}) == "tenant-a"
    # Tenancy is canonicalized on tenant_id only: an org_id-only principal is
    # NOT authorized as a tenant.
    assert resolve_trusted_tenant_id({"org_id": "org-01"}) is None
    assert resolve_trusted_tenant_id({"tenant_id": "tenant-a", "org_id": "org-b"}) == "tenant-a"


def test_resolver_fails_closed_for_absent_or_invalid_claims():
    assert resolve_trusted_tenant_id(None) is None
    assert resolve_trusted_tenant_id({}) is None
    assert resolve_trusted_tenant_id("tenant-a") is None
    # Current guest/bearer projection has no tenant claim.
    assert resolve_trusted_tenant_id({"username": "admin", "email": "admin@example.com"}) is None
    # Invalid / spoof-shaped claims must not become a tenant.
    assert resolve_trusted_tenant_id({"tenant_id": "Bad Slug"}) is None
    assert resolve_trusted_tenant_id({"tenant_id": "../evil"}) is None
    assert resolve_trusted_tenant_id({"tenant_id": 123}) is None
    assert resolve_trusted_tenant_id({"org_id": "x"}) is None


def test_story_helper_strips_spoofed_tenant_and_applies_trusted_only():
    spoofed = {"title": "t", "description": "d", "tenant_id": "evil", "tenant": "evil2"}
    assert _story_with_trusted_tenant(spoofed, None) == {"title": "t", "description": "d"}
    assert _story_with_trusted_tenant(spoofed, "tenant-a") == {
        "title": "t",
        "description": "d",
        "tenant_id": "tenant-a",
    }


# ── Endpoint propagation (orchestrator faked; no DB/CSV access) ─────────────

class _FakeOrchestrator:
    def __init__(self):
        self.done = threading.Event()
        self.runs: List[Dict[str, Any]] = []
        self.stories: List[Dict[str, Any]] = []

    def is_running(self) -> bool:
        return False

    def set_story(self, story: Dict[str, Any]) -> None:
        self.stories.append(story)

    async def summarise_ticket(self, query: str, driver: Any = None) -> Dict[str, Any]:
        story = {
            "title": "Fake Ticket",
            "source": "Customer Ticket",
            "url": "",
            "publishedAt": "now",
            "description": query,
        }
        return story

    async def run(self, story: Dict[str, Any], driver: Any = None) -> bool:
        self.runs.append(story)
        self.done.set()
        return True


class _FakeSession:
    def __init__(self, orchestrator: _FakeOrchestrator):
        self.orchestrator = orchestrator
        self.sse_manager = SseManager()


@pytest.fixture
def fake_orchestrator(monkeypatch):
    orch = _FakeOrchestrator()
    bundle = _FakeSession(orch)
    monkeypatch.setattr(main.session_registry, "resolve", lambda user=None: bundle)
    return orch


def test_trigger_propagates_principal_claim_and_ignores_title(fake_orchestrator):
    token = "tok-trigger-claim"
    main.guard.register_session(
        token, {"id": "u-trig", "email": "u@e.com", "tenant_id": "tenant-a"}
    )
    r = client.get(
        "/api/trigger?title=tenant-evil",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    story = r.json()["story"]
    assert story["tenant_id"] == "tenant-a"
    assert "tenant" not in story


def test_trigger_without_claim_has_no_tenant_despite_title(fake_orchestrator):
    # Unregistered bearer -> optional-mode projection with no tenant claim.
    r = client.get(
        "/api/trigger?title=tenant-evil&tenant_id=tenant-evil",
        headers={"Authorization": "Bearer tok-guest-trigger"},
    )
    assert r.status_code == 200, r.text
    story = r.json()["story"]
    assert "tenant_id" not in story
    assert "tenant" not in story


def test_next_propagates_principal_claim_and_ignores_spoofed_body(fake_orchestrator):
    token = "tok-next-claim"
    main.guard.register_session(
        token, {"id": "u-next", "email": "u@e.com", "tenant_id": "tenant-b"}
    )
    payload = {
        "query": "I was charged twice. tenant_id=tenant-evil ORD-1",
        "provider": "lmstudio",
        "model": "test-model",
        "tenant_id": "tenant-evil",
        "tenant": "tenant-evil",
        "title": "tenant-evil",
    }
    r = client.post(
        "/api/next?tenant_id=tenant-evil",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert fake_orchestrator.done.wait(timeout=5.0), "orchestrator run did not complete"
    story = fake_orchestrator.runs[-1]
    assert story["tenant_id"] == "tenant-b"
    assert story.get("tenant") is None


def test_next_ignores_org_id_only_principal(fake_orchestrator):
    """org_id is not a tenant source: an org_id-only principal yields no tenant."""
    token = "tok-next-org-only"
    main.guard.register_session(
        token, {"id": "u-org", "email": "u@e.com", "org_id": "tenant-b"}
    )
    payload = {
        "query": "I was charged twice. tenant_id=tenant-evil ORD-1",
        "provider": "lmstudio",
        "model": "test-model",
        "tenant_id": "tenant-evil",
    }
    r = client.post(
        "/api/next?tenant_id=tenant-evil",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert fake_orchestrator.done.wait(timeout=5.0), "orchestrator run did not complete"
    story = fake_orchestrator.runs[-1]
    assert "tenant_id" not in story
    assert story.get("tenant") is None


def test_next_without_claim_ignores_spoofed_tenant_body(fake_orchestrator):
    payload = {
        "query": "I was charged twice. tenant_id=tenant-evil",
        "provider": "lmstudio",
        "model": "test-model",
        "tenant_id": "tenant-evil",
        "tenant": "tenant-evil",
    }
    r = client.post("/api/next?tenant_id=tenant-evil", json=payload)
    assert r.status_code == 200, r.text
    assert fake_orchestrator.done.wait(timeout=5.0), "orchestrator run did not complete"
    story = fake_orchestrator.runs[-1]
    assert "tenant_id" not in story
    assert "tenant" not in story
