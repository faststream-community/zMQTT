"""Python version compatibility helpers."""

import asyncio
import contextlib
import sys
from collections.abc import AsyncGenerator, Awaitable
from typing import Any, TypeVar

if sys.version_info >= (3, 11):
    from typing import Self
else:
    from typing_extensions import Self

__all__ = ("Self", "defer_cancellation", "wait_for")

_T = TypeVar("_T")

if sys.version_info >= (3, 12):
    wait_for = asyncio.wait_for  # noqa: TID251 - fixed in 3.12 (python/cpython#96764)

else:

    async def wait_for(aw: Awaitable[_T], timeout: float | None) -> _T:
        """``asyncio.wait_for()`` that never hides a cancellation.

        Before 3.12, ``asyncio.wait_for()`` returns the result instead of
        raising ``CancelledError`` when the awaited task has already finished
        by the time the cancellation is handled (python/cpython#86296).
        """
        if timeout is None:
            return await aw
        task = asyncio.ensure_future(aw)
        try:
            await asyncio.wait((task,), timeout=timeout)
        except asyncio.CancelledError:
            await _cancel_and_wait(task)
            raise
        if task.done():
            return task.result()
        await _cancel_and_wait(task)
        try:
            return task.result()
        except asyncio.CancelledError as e:
            raise asyncio.TimeoutError from e

    async def _cancel_and_wait(task: asyncio.Future[Any]) -> None:
        task.cancel()
        await asyncio.wait((task,))
        if not task.cancelled():
            task.exception()  # mark retrieved: the caller reports its own outcome


if sys.version_info >= (3, 11):

    @contextlib.asynccontextmanager
    async def defer_cancellation() -> AsyncGenerator[None, None]:
        """Temporarily remove pending cancellations, restore them after cleanup."""
        task = asyncio.current_task()
        cancels = task.cancelling() if task else 0
        for _ in range(cancels):
            if task:
                task.uncancel()
        try:
            yield
        finally:
            for _ in range(cancels):
                if task:
                    task.cancel()

else:

    @contextlib.asynccontextmanager
    async def defer_cancellation() -> AsyncGenerator[None, None]:
        """No-op on Python < 3.11: cancelling()/uncancel() are not available."""
        yield
