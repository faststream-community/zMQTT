"""Correlation-based routing for MQTT 5.0 request/response messages."""

import asyncio
from dataclasses import dataclass, field
from typing import Protocol, TypeAlias

from zmqtt._internal.types.message import Message
from zmqtt.errors import MQTTDisconnectedError

_RequestKey: TypeAlias = tuple[str, bytes]


class _ResponseSubscriptionManager(Protocol):
    async def add_response_observer(self, topic: str) -> None: ...

    async def remove_response_observer(self, topic: str) -> None: ...


@dataclass(slots=True)
class _PendingRequest:
    """A registered response waiter with explicit, idempotent cleanup."""

    future: asyncio.Future[Message]
    _dispatcher: "_RequestDispatcher"
    _key: _RequestKey
    _closed: bool = field(default=False, init=False)
    claimed: bool = field(default=False, init=False)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._dispatcher.unregister(self._key, self)
        finally:
            if self.future.done() and not self.future.cancelled():
                self.future.exception()


@dataclass(slots=True)
class _TopicInterest:
    """Shared broker-subscription readiness for one response topic."""

    refcount: int = 0
    ready: asyncio.Task[None] | None = None


@dataclass(slots=True)
class _ClaimedResponse:
    """A response reserved for one pending request on one connection."""

    dispatcher: "_RequestDispatcher"
    pending: _PendingRequest
    key: _RequestKey
    message: Message
    generation: int

    def deliver(self) -> None:
        self.dispatcher.complete(self)


class _RequestDispatcher:
    """Route matching responses exclusively to request futures.

    Only active waiters are retained. Incoming messages without a matching
    waiter are not buffered here and remain available to ordinary subscription
    routing, so late and unsolicited responses cannot grow dispatcher state.
    """

    def __init__(self, max_pending_requests: int) -> None:
        if max_pending_requests <= 0:
            msg = "max_pending_requests must be positive"
            raise ValueError(msg)
        self._capacity = asyncio.Semaphore(max_pending_requests)
        self._pending: dict[_RequestKey, _PendingRequest] = {}
        self._topics: dict[str, _TopicInterest] = {}
        self._protocol: _ResponseSubscriptionManager | None = None
        self._generation = 0

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def bind(self, protocol: _ResponseSubscriptionManager) -> None:
        """Bind the dispatcher to the current connection's protocol engine."""
        self._protocol = protocol
        self._generation += 1
        for pending in self._pending.values():
            pending.claimed = False
        for interest in self._topics.values():
            interest.ready = None

    async def restore(self) -> None:
        """Restore response-topic interests after protocol reconnection."""
        for topic, interest in tuple(self._topics.items()):
            if interest.refcount > 0:
                await asyncio.shield(self._ensure_topic_ready(topic, interest))

    async def register(self, topic: str, correlation_data: bytes) -> _PendingRequest:
        """Register a bounded response waiter before its request is published."""
        await self._capacity.acquire()
        key = (topic, correlation_data)
        pending: _PendingRequest | None = None
        registered = False
        try:
            self._ensure_unique(key)
            self._require_protocol()

            interest = self._topics.get(topic)
            if interest is None:
                interest = _TopicInterest()
                self._topics[topic] = interest

            pending = _PendingRequest(
                future=asyncio.get_running_loop().create_future(),
                _dispatcher=self,
                _key=key,
            )
            self._pending[key] = pending
            interest.refcount += 1
            await asyncio.shield(self._ensure_topic_ready(topic, interest))
            registered = True
            return pending
        finally:
            if not registered:
                if pending is None:
                    self._capacity.release()
                else:
                    await self.unregister(key, pending)

    def claim(self, message: Message) -> _ClaimedResponse | None:
        """Atomically reserve a matching waiter and return the claimed response."""
        properties = message.properties
        if properties is None or properties.correlation_data is None:
            return None

        key = (message.topic, properties.correlation_data)
        pending = self._pending.get(key)
        if pending is None or pending.claimed or pending.future.done():
            return None
        pending.claimed = True
        return _ClaimedResponse(
            dispatcher=self,
            pending=pending,
            key=key,
            message=message,
            generation=self._generation,
        )

    def complete(self, response: _ClaimedResponse) -> None:
        """Deliver a claimed response if it still belongs to this connection."""
        if (
            self._generation == response.generation
            and self._pending.get(response.key) is response.pending
            and not response.pending.future.done()
        ):
            response.pending.future.set_result(response.message)

    async def unregister(
        self,
        key: _RequestKey,
        pending: _PendingRequest,
    ) -> None:
        """Remove one waiter and release its response-topic interest."""
        removed = False
        try:
            if self._pending.get(key) is not pending:
                return

            removed = True
            del self._pending[key]
            if not pending.future.done():
                pending.future.cancel()

            topic = key[0]
            interest = self._topics[topic]
            interest.refcount -= 1
            if interest.refcount > 0:
                return

            observer_active = False
            try:
                ready = interest.ready
                if ready is not None:
                    await asyncio.shield(ready)
                    observer_active = True
            finally:
                # A new waiter may have joined while the initial SUBACK was
                # pending. In that case it inherits the same topic interest.
                if interest.refcount == 0 and self._topics.get(topic) is interest:
                    del self._topics[topic]
                    protocol = self._protocol
                    if observer_active and protocol is not None:
                        await protocol.remove_response_observer(topic)
        finally:
            if removed:
                self._capacity.release()

    async def cancel_pending(self) -> None:
        """Fail and forget every waiter when the client shuts down."""
        pending = tuple(self._pending.values())
        ready_tasks = tuple(interest.ready for interest in self._topics.values() if interest.ready is not None)
        self._pending.clear()
        self._topics.clear()
        self._protocol = None
        self._generation += 1
        for request in pending:
            if not request.future.done():
                request.future.set_exception(MQTTDisconnectedError("Client disconnected"))
            self._capacity.release()
        for task in ready_tasks:
            task.cancel()
        await asyncio.gather(*ready_tasks, return_exceptions=True)

    def _ensure_topic_ready(self, topic: str, interest: _TopicInterest) -> asyncio.Task[None]:
        ready = interest.ready
        if ready is None:
            protocol = self._require_protocol()
            ready = asyncio.create_task(protocol.add_response_observer(topic))
            interest.ready = ready
        return ready

    def _require_protocol(self) -> _ResponseSubscriptionManager:
        if self._protocol is None:
            msg = "Not connected"
            raise MQTTDisconnectedError(msg)
        return self._protocol

    def _ensure_unique(self, key: _RequestKey) -> None:
        if key in self._pending:
            msg = "correlation_data is already in use for this response_topic"
            raise ValueError(msg)
