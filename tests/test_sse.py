import asyncio
import json
import pytest
from app.http.sse_manager import SseManager


@pytest.mark.asyncio
async def test_sse_broadcast_and_subscription():
    sse = SseManager()
    assert sse.client_count == 0

    q1 = asyncio.Queue()
    q2 = asyncio.Queue()

    sse.add_client(q1)
    sse.add_client(q2)
    assert sse.client_count == 2

    sse.broadcast("council_start", {"title": "Test Policy"})

    frame1 = await q1.get()
    frame2 = await q2.get()

    assert "event: council_start\n" in frame1
    assert '"title": "Test Policy"' in frame1
    assert frame1 == frame2

    sse.remove_client(q1)
    assert sse.client_count == 1

    sse.broadcast("agent", {"state": "Delhi", "message": "Statement"})
    assert q1.empty()
    assert not q2.empty()

    frame_q2 = await q2.get()
    assert "event: agent\n" in frame_q2


@pytest.mark.asyncio
async def test_sse_status_throttling():
    sse = SseManager()
    q = asyncio.Queue()
    sse.add_client(q)

    # First notification goes through
    first = sse.notify_status("Analyzing policy...", min_interval_ms=10000)
    assert first is True
    assert not q.empty()
    await q.get()

    # Immediate duplicate notification is throttled
    second = sse.notify_status("Analyzing policy...", min_interval_ms=10000)
    assert second is False
    assert q.empty()

    # Different notification goes through
    third = sse.notify_status("Round 1 starting...", min_interval_ms=10000)
    assert third is True
    assert not q.empty()
