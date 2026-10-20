import asyncio
import json
import pytest
from unittest.mock import patch
from app.config import Settings
from app.providers.openai_driver import OpenAiDriver
from app.simulation import CouncilOrchestrator, SimulationState
from app.http.sse_manager import SseManager
from fastapi.testclient import TestClient
from app.main import app


def test_settings_show_reasoning_toggle(monkeypatch):
    monkeypatch.setenv("SHOW_REASONING", "true")
    s = Settings()
    assert s.show_reasoning is True

    monkeypatch.setenv("SHOW_REASONING", "false")
    s2 = Settings()
    assert s2.show_reasoning is False

    monkeypatch.setenv("SHOW_REASONING", "1")
    s3 = Settings()
    assert s3.show_reasoning is True


@pytest.mark.asyncio
async def test_openai_driver_instant_reasoning_streaming():
    driver = OpenAiDriver(provider="lmstudio", base_url="http://localhost:1235/v1")

    # Simulated SSE lines matching LM Studio streaming format
    sse_lines = [
        b'data: {"choices":[{"delta":{"reasoning_content":"Step 1: analyze "}}]}\n\n',
        b'data: {"choices":[{"delta":{"reasoning_content":"impact on Tamil Nadu."}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"Tamil Nadu strongly "}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"supports this policy."}}]}\n\n',
        b'data: [DONE]\n\n',
    ]

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        async def aiter_lines(self):
            for line in sse_lines:
                yield line.decode("utf-8")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        def stream(self, method, url, **kwargs):
            return FakeResponse()

    reasoning_chunks = []
    content_chunks = []

    with patch("httpx.AsyncClient", FakeClient):
        result = await driver.stream(
            [{"role": "user", "content": "test"}],
            max_tokens=4096,
            on_delta=lambda c: content_chunks.append(c),
            on_reasoning_delta=lambda r: reasoning_chunks.append(r),
        )

    assert "".join(reasoning_chunks) == "Step 1: analyze impact on Tamil Nadu."
    assert "".join(content_chunks) == "Tamil Nadu strongly supports this policy."
    assert "Tamil Nadu strongly supports this policy." in result


@pytest.mark.asyncio
async def test_orchestrator_broadcasts_reasoning_when_enabled():
    class StreamingDriver:
        async def stream(self, messages, max_tokens, on_delta=None, on_reasoning_delta=None, **kwargs):
            if on_reasoning_delta:
                on_reasoning_delta("Thinking about education policy...")
            if on_delta:
                on_delta("Tamil Nadu opposes this bill.")
            return "Tamil Nadu opposes this bill."

    # Test 1: show_reasoning is True
    sse = SseManager()
    events = []
    sse.broadcast = lambda event, data: events.append((event, data))

    cfg = Settings(show_reasoning=True)
    orch = CouncilOrchestrator(
        config=cfg,
        driver=StreamingDriver(),
        sse_manager=sse,
        state=SimulationState(),
        enable_barrier=False,
    )

    agent = {"name": "Tamil Nadu Council", "state": "Tamil Nadu"}
    msg = await orch._agent_speak(agent, [{"role": "user", "content": "speak"}], 4096, 0, "Declaration")

    assert msg == "Tamil Nadu opposes this bill."
    event_names = [e[0] for e in events]
    assert "agent_reasoning_delta" in event_names
    assert "agent_delta" in event_names
    assert "agent" in event_names

    # Check reasoning payload
    reasoning_event = next(d for e, d in events if e == "agent_reasoning_delta")
    assert reasoning_event["delta"] == "Thinking about education policy..."


@pytest.mark.asyncio
async def test_orchestrator_suppresses_reasoning_when_disabled():
    class StreamingDriver:
        async def stream(self, messages, max_tokens, on_delta=None, on_reasoning_delta=None, **kwargs):
            if on_reasoning_delta:
                on_reasoning_delta("Hidden internal CoT...")
            if on_delta:
                on_delta("Official speech text.")
            return "Official speech text."

    # Test 2: show_reasoning is False
    sse = SseManager()
    events = []
    sse.broadcast = lambda event, data: events.append((event, data))

    cfg = Settings(show_reasoning=False)
    orch = CouncilOrchestrator(
        config=cfg,
        driver=StreamingDriver(),
        sse_manager=sse,
        state=SimulationState(),
        enable_barrier=False,
    )

    agent = {"name": "Tamil Nadu Council", "state": "Tamil Nadu"}
    await orch._agent_speak(agent, [{"role": "user", "content": "speak"}], 4096, 0, "Declaration")

    event_names = [e[0] for e in events]
    assert "agent_reasoning_delta" not in event_names
    assert "agent_delta" in event_names
    assert "agent" in event_names


def test_api_config_show_reasoning_get_and_post():
    client = TestClient(app)
    headers = {"Authorization": "Bearer test_token"}

    # GET /api/config should contain showReasoning
    res = client.get("/api/config", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "showReasoning" in data

    # POST /api/config should update showReasoning
    update_res = client.post(
        "/api/config",
        headers=headers,
        json={"provider": "lmstudio", "model": "qwen/qwen3.5-9b", "showReasoning": True},
    )
    assert update_res.status_code == 200
    assert update_res.json()["showReasoning"] is True
