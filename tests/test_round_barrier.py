import asyncio
import pytest
from app.simulation.round_barrier import RoundBarrier, normalize_round_number


def test_normalize_round_number():
    assert normalize_round_number(0) == 0
    assert normalize_round_number("1") == 1
    assert normalize_round_number(2.0) == 2
    assert normalize_round_number(-1) is None
    assert normalize_round_number("invalid") is None
    assert normalize_round_number(None) is None


@pytest.mark.asyncio
async def test_round_barrier_wait_and_signal():
    barrier = RoundBarrier()
    waiting_called = []

    async def waiter():
        await barrier.wait_for_round_begin(1, on_waiting=lambda r: waiting_called.append(r))

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.01)

    assert waiting_called == [1]
    assert not task.done()

    barrier.mark_round_begun(1)
    await asyncio.sleep(0.01)
    assert task.done()


@pytest.mark.asyncio
async def test_round_barrier_pre_signalled():
    barrier = RoundBarrier()
    barrier.mark_round_begun(0)

    waiting_called = []
    # Should resolve immediately without waiting callback
    await barrier.wait_for_round_begin(0, on_waiting=lambda r: waiting_called.append(r))
    assert len(waiting_called) == 0
