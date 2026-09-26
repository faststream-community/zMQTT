"""zmqtt._internal._compat.wait_for keeps asyncio.wait_for() semantics without losing cancellations."""

import asyncio

import pytest

from zmqtt._internal._compat import wait_for


async def test_result_and_errors_pass_through() -> None:
    async def answer() -> int:
        await asyncio.sleep(0)
        return 42

    async def fail() -> None:
        await asyncio.sleep(0)
        msg = "boom"
        raise ValueError(msg)

    assert await wait_for(answer(), timeout=5) == 42
    assert await wait_for(answer(), timeout=None) == 42
    with pytest.raises(ValueError, match="boom"):
        await wait_for(fail(), timeout=5)


async def test_timeout_cancels_awaitable() -> None:
    inner = asyncio.ensure_future(asyncio.Event().wait())

    with pytest.raises(asyncio.TimeoutError):
        await wait_for(inner, timeout=0.01)

    assert inner.cancelled()


async def test_cancelled_awaitable_is_not_reported_as_timeout() -> None:
    inner = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(wait_for(inner, timeout=5))
    await asyncio.sleep(0)
    inner.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_accepted_cancellation_is_never_lost() -> None:
    finished = asyncio.Event()

    async def inner() -> str:
        await asyncio.sleep(0)
        finished.set()
        return "result"

    task = asyncio.create_task(wait_for(inner(), timeout=5))
    await finished.wait()
    # Before 3.12 the inner task is done but wait_for() has not resumed yet:
    # asyncio.wait_for() returned "result" here (python/cpython#86296).
    accepted = task.cancel()
    await asyncio.wait((task,))

    assert task.cancelled() == accepted
