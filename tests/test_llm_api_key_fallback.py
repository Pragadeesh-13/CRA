"""
Regression tests for the empty-apiKey -> 401 -> MockDriver fallback bug.

Root cause: the browser UI sends ``{apiKey: ""}`` for local providers
(lmstudio). ``app/main.py`` used ``body.get("apiKey") if ... is not None``,
so the empty string clobbered the configured ``settings.llm_api_key``, and
``OpenAiDriver._build_headers`` used ``api_key_override if ... is not None``,
so the empty override clobbered the instance key too. llama-server then
rejected the request with 401 and the driver silently fell back to
MockDriver — no request ever reached the real model.

These tests prove empty/whitespace overrides always fall back to the
configured key, and that a request built with ``apiKey: ""`` authenticates
against an auth-enforcing fake llama-server without MockDriver fallback.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app, key_vault
from app.providers.openai_driver import OpenAiDriver

FAKE_KEY = "fake-llama-server-key-for-tests"
FAKE_MODEL = "test-model-xyz"


# ---------------------------------------------------------------------------
# Fake llama-server: requires Bearer FAKE_KEY, otherwise 401.
# ---------------------------------------------------------------------------


class _FakeLlamaServer:
    def __init__(self, expected_key: str):
        self.expected_key = expected_key
        self.auth_headers = []
        self.requests = 0
        server_self = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence test output
                pass

            def _handle(self):
                server_self.requests += 1
                auth = self.headers.get("Authorization", "")
                server_self.auth_headers.append(auth)
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                if auth != f"Bearer {server_self.expected_key}":
                    body = json.dumps({"error": "Unauthorized"}).encode()
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path.endswith("/chat/completions"):
                    body = json.dumps(
                        {"choices": [{"message": {"content": "FAKE-LLAMA-RESPONSE"}}]}
                    ).encode()
                elif self.path.endswith("/models"):
                    body = json.dumps({"data": [{"id": FAKE_MODEL}]}).encode()
                else:
                    body = json.dumps({"ok": True}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_POST = _handle
            do_GET = _handle

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        return False

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"


# ---------------------------------------------------------------------------
# Driver header resolution
# ---------------------------------------------------------------------------


class TestDriverHeaderFallback:
    def test_empty_instance_key_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", FAKE_KEY)
        driver = OpenAiDriver(provider="lmstudio", api_key="")
        assert driver._build_headers()["Authorization"] == f"Bearer {FAKE_KEY}"

    def test_empty_override_does_not_clobber_env_key(self, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", FAKE_KEY)
        driver = OpenAiDriver(provider="lmstudio", api_key="")
        assert driver._build_headers("")["Authorization"] == f"Bearer {FAKE_KEY}"
        assert driver._build_headers("   ")["Authorization"] == f"Bearer {FAKE_KEY}"

    def test_empty_override_does_not_clobber_instance_key(self, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "env-key-should-not-win")
        driver = OpenAiDriver(provider="lmstudio", api_key="instance-key")
        assert driver._build_headers("")["Authorization"] == "Bearer instance-key"
        assert driver._build_headers(None)["Authorization"] == "Bearer instance-key"

    def test_explicit_override_wins(self, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", FAKE_KEY)
        driver = OpenAiDriver(provider="lmstudio", api_key="instance-key")
        assert driver._build_headers("explicit-key")["Authorization"] == "Bearer explicit-key"

    def test_lmstudio_placeholder_only_when_no_key_anywhere(self, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        driver = OpenAiDriver(provider="lmstudio", api_key="")
        assert driver._build_headers("")["Authorization"] == "Bearer lm-studio"

    def test_no_placeholder_for_cloud_providers(self, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        driver = OpenAiDriver(provider="openai", api_key="")
        assert "Authorization" not in driver._build_headers("")


# ---------------------------------------------------------------------------
# End-to-end against a fake auth-enforcing llama-server
# ---------------------------------------------------------------------------


class TestFakeLlamaServerAuth:
    @pytest.mark.asyncio
    async def test_driver_authenticates_with_empty_key_and_never_falls_back(self, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", FAKE_KEY)
        with _FakeLlamaServer(FAKE_KEY) as server:
            driver = OpenAiDriver(provider="lmstudio", api_key="", base_url=server.base_url)
            # apiKey: "" override, exactly as the UI / pipeline may supply it.
            output = await driver.complete(
                [{"role": "user", "content": "Hello"}], apiKey=""
            )
            assert output == "FAKE-LLAMA-RESPONSE"
            assert driver._offline_detected is False
            assert server.auth_headers
            assert all(header == f"Bearer {FAKE_KEY}" for header in server.auth_headers)

    def test_api_next_with_empty_apikey_uses_configured_key_and_model(self, monkeypatch):
        """POST /api/next {apiKey: "", model: ""} must not send an empty key."""
        monkeypatch.setenv("LLM_API_KEY", FAKE_KEY)
        monkeypatch.setattr(settings, "llm_api_key", FAKE_KEY)
        monkeypatch.setattr(settings, "llm_model", FAKE_MODEL)

        guest_before = dict(key_vault._guest_slot)
        try:
            with _FakeLlamaServer(FAKE_KEY) as server:
                monkeypatch.setenv("LMSTUDIO_BASE_URL", server.base_url)
                client = TestClient(app)
                res = client.post(
                    "/api/next",
                    json={
                        "query": "How do I reset my password?",
                        "provider": "lmstudio",
                        "model": "",
                        "apiKey": "",
                    },
                )
                assert res.status_code == 200
                assert res.json().get("ok") is True

                # The vault must hold the *resolved* key/model, not "".
                stored = key_vault._guest_slot
                assert stored.get("apiKey") == FAKE_KEY
                assert stored.get("model") == FAKE_MODEL

                # The background resolution must actually reach the llama
                # server with the configured key (i.e. no MockDriver fallback).
                deadline = time.time() + 10
                while time.time() < deadline and not server.auth_headers:
                    time.sleep(0.1)
                assert server.auth_headers, "fake llama-server received no requests"
                assert all(header == f"Bearer {FAKE_KEY}" for header in server.auth_headers)
        finally:
            key_vault._guest_slot.clear()
            key_vault._guest_slot.update(guest_before)

    def test_api_next_generic_model_falls_back_to_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_api_key", FAKE_KEY)
        monkeypatch.setattr(settings, "llm_model", FAKE_MODEL)
        guest_before = dict(key_vault._guest_slot)
        try:
            client = TestClient(app)
            res = client.post(
                "/api/next",
                json={
                    "query": "How do I reset my password?",
                    "provider": "lmstudio",
                    "model": "generic",
                    "apiKey": "   ",
                },
            )
            assert res.status_code == 200
            stored = key_vault._guest_slot
            assert stored.get("model") == FAKE_MODEL
            assert stored.get("apiKey") == FAKE_KEY
        finally:
            key_vault._guest_slot.clear()
            key_vault._guest_slot.update(guest_before)
