import asyncio
import pytest
from app.config import settings
from app.providers.mock_driver import MockDriver
from app.simulation import SimulationState, RoundBarrier, CouncilOrchestrator
from app.http.sse_manager import SseManager
from app.reference import execute_agent_with_reasoning


@pytest.mark.asyncio
async def test_resolution_pipeline_lifecycle():
    mock_driver = MockDriver()
    sse = SseManager()
    state = SimulationState()

    events_received = []
    sse.broadcast = lambda event, data: events_received.append((event, data))

    orch = CouncilOrchestrator(
        config=settings,
        driver=mock_driver,
        sse_manager=sse,
        state=state,
        enable_barrier=False,
    )

    story = {
        "title": "Delayed Delivery Refund Request",
        "source": "Complaint Test",
        "url": "",
        "description": "Customer requested full refund after delivery was delayed by 14 days without notice.",
    }

    success = await orch.run(story)
    assert success is True

    event_names = [e[0] for e in events_received]

    # Verify lifecycle SSE events occurred in order
    assert "council_start" in event_names
    assert "round_start" in event_names
    assert "system" in event_names
    assert "council_end" in event_names

    end_event = next(d for e, d in events_received if e == "council_end")
    assert "summary" in end_event
    assert "impacts" in end_event


@pytest.mark.asyncio
async def test_reference_reasoning_and_output_execution():
    mock_driver = MockDriver()
    sse = SseManager()
    events = []
    sse.broadcast = lambda event, data: events.append((event, data))

    res = await execute_agent_with_reasoning(
        driver=mock_driver,
        sse_manager=sse,
        agent_name="Troy",
        role_or_state="Complaint Agent",
        messages=[{"role": "user", "content": "Help with refund"}],
        show_reasoning=True,
        use_stream=True,
    )

    assert "refund request has been accepted" in res["message"]
    assert "Evaluating complaint details" in res["reasoning"]

    event_types = [e[0] for e in events]
    assert "agent_start" in event_types
    assert "agent_delta" in event_types
    assert "agent" in event_types
