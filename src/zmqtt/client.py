"""High-level MQTT client — public API layer."""

import asyncio
import contextlib
import dataclasses
import logging
import os
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Final, Literal, Protocol, overload

from typing_extensions import Self

from zmqtt._internal._compat import defer_cancellation
from zmqtt._internal.packets.auth import Auth
from zmqtt._internal.packets.connect import Connect
from zmqtt._internal.packets.properties import (
    AuthProperties,
    ConnectProperties,
    PublishProperties,
)
from zmqtt._internal.packets.publish import Publish
from zmqtt._internal.packets.subscribe import SubscriptionRequest
from zmqtt._internal.protocol import MQTTProtocol
from zmqtt._internal.state import SessionState
from zmqtt._internal.transport.base import Transport
from zmqtt._internal.transport.tcp import open_tcp
from zmqtt._internal.transport.tls import open_tls
from zmqtt._internal.types.message import Message
from zmqtt._internal.types.qos import QoS
from zmqtt._internal.types.topic import validate_publish, validate_response_topic, validate_subscribe_topic
from zmqtt.errors import MQTTConnectError, MQTTDisconnectedError, MQTTTimeoutError

__all__ = (
    "MQTTClient",
    "MQTTClientV5",
    "MQTTClientV311",
    "ReconnectConfig",
    "Subscription",
    "Transport",
    "create_client",
)

TransportFactory = Callable[[str, int, ssl.SSLContext | bool | None], Awaitable[Transport]]

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReconnectConfig:
    """Configuration for automatic reconnection on connection loss.

    Attributes:
        enabled: Whether to reconnect automatically. Set to ``False`` to let
            exceptions propagate immediately on disconnection.
        initial_delay: Seconds to wait before the first reconnection attempt.
        max_delay: Upper bound (seconds) for the exponential back-off delay.
        backoff_factor: Multiplier applied to the delay after each failed attempt.
        max_attempts: Maximum total number of connection attempts before
            giving up. ``None`` retries indefinitely.
    """

    enabled: bool = True
    initial_delay: float = 1.0
    max_delay: float = 60.0
    backoff_factor: float = 2.0
    max_attempts: int | None = 5


async def _default_transport_factory(
    host: str,
    port: int,
    tls: ssl.SSLContext | bool | None,
) -> Transport:
    if not tls:
        return await open_tcp(host, port)
    if tls is True:
        return await open_tls(host, port)
    return await open_tls(host, port, tls)


class MQTTClientV311(Protocol):
    async def __aenter__(self) -> Self: ...

    async def __aexit__(self, *exc: object) -> None: ...

    async def publish(
        self,
        topic: str,
        payload: bytes | str,
        *,
        qos: QoS = QoS.AT_MOST_ONCE,
        retain: bool = False,
    ) -> None: ...

    def subscribe(
        self,
        *filters: str,
        qos: QoS = QoS.AT_MOST_ONCE,
        auto_ack: bool = True,
        receive_buffer_size: int = 1000,
    ) -> "Subscription": ...

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def ping(self, timeout: float = 10.0) -> float: ...


class MQTTClientV5(Protocol):
    """Type-safe view of MQTTClient for MQTT 5.0 connections."""

    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, *exc: object) -> None: ...

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def publish(
        self,
        topic: str,
        payload: bytes | str,
        *,
        qos: QoS = QoS.AT_MOST_ONCE,
        retain: bool = False,
        properties: PublishProperties | None = None,
    ) -> None: ...

    def subscribe(
        self,
        *filters: str,
        qos: QoS = QoS.AT_MOST_ONCE,
        auto_ack: bool = True,
        receive_buffer_size: int = 1000,
        no_local: bool = False,
        retain_as_published: bool = False,
    ) -> "Subscription": ...

    async def auth(self, method: str, data: bytes | None = None) -> None: ...

    async def ping(self, timeout: float = 10.0) -> float: ...

    async def request(
        self,
        topic: str,
        payload: bytes | str,
        *,
        qos: QoS = QoS.AT_MOST_ONCE,
        timeout: float = 30.0,
        properties: PublishProperties | None = None,
    ) -> "Message": ...


class Subscription:
    """Async context manager for an active topic subscription.

    Registers filters on enter, unsubscribes on exit. Messages are available
    via get_message() or async iteration. Survives reconnection transparently —
    the queue keeps buffering and delivery resumes when the connection restores.
    """

    def __init__(
        self,
        client: "MQTTClient",
        filters: list[str],
        qos: QoS,
        auto_ack: bool = True,
        receive_buffer_size: int = 1000,
        no_local: bool = False,
        retain_as_published: bool = False,
    ) -> None:
        self._client = client
        self._filters = filters
        self._qos = qos
        self._auto_ack = auto_ack
        self._queue: asyncio.Queue[Message] = asyncio.Queue(receive_buffer_size)
        self._relay_tasks: list[asyncio.Task[None]] = []
        self._no_local = no_local
        self._retain_as_published = retain_as_published
        self._registered_filters: list[str] = []

    async def __aenter__(self) -> Self:
        """Register the subscription filters with the broker.

        Raises:
            MQTTDisconnectedError: If the client is not currently connected.
        """
        if self._client._protocol is None:
            msg = "Not connected"
            raise MQTTDisconnectedError(msg)
        self._client._subscriptions.append(self)
        await self._do_subscribe(self._client._protocol)
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Unsubscribe from all filters and stop message delivery."""
        self._client._subscriptions.remove(self)
        await self._cancel_relays()
        being_cancelled = isinstance(exc[1], asyncio.CancelledError)
        if not being_cancelled and self._registered_filters and self._client._protocol is not None:
            with contextlib.suppress(Exception):
                await self._client._protocol.unsubscribe(self._registered_filters)

    async def _do_subscribe(self, protocol: MQTTProtocol) -> None:
        reqs = [
            SubscriptionRequest(
                topic_filter=f,
                qos=self._qos,
                no_local=self._no_local,
                retain_as_published=self._retain_as_published,
            )
            for f in self._filters
        ]
        _, queues = await protocol.subscribe(reqs, auto_ack=self._auto_ack)
        self._registered_filters = list(queues.keys())
        for q in queues.values():
            task: asyncio.Task[None] = asyncio.create_task(self._relay_loop(q))
            self._relay_tasks.append(task)

    async def _cancel_relays(self) -> None:
        for t in self._relay_tasks:
            t.cancel()
        await asyncio.gather(*self._relay_tasks, return_exceptions=True)
        self._relay_tasks.clear()

    async def _reconnect(self, protocol: MQTTProtocol) -> None:
        """Re-subscribe on a fresh protocol after reconnection."""
        await self._cancel_relays()
        await self._do_subscribe(protocol)

    async def _relay_loop(self, source: asyncio.Queue[Message]) -> None:
        while True:
            msg = await source.get()
            await self._queue.put(msg)

    async def start(self) -> None:
        """Register the subscription filters with the broker.
        Equivalent to entering the async context manager.
        Must be paired with a corresponding :meth:`stop` call to send
        UNSUBSCRIBE and release internal resources.

        Example::

            sub = client.subscribe("sensors/#", qos=QoS.AT_LEAST_ONCE)
            await sub.start()
            # ... later
            await sub.stop()

        Raises:
            MQTTDisconnectedError: If the client is not currently connected.
        """
        await self.__aenter__()

    async def stop(self) -> None:
        """Unsubscribe from all filters and stop message delivery.

        Equivalent to exiting the async context manager. Sends UNSUBSCRIBE to
        the broker and cancels internal relay tasks. Safe to call even if the
        connection has already been lost — the UNSUBSCRIBE is silently skipped
        in that case.

        Example::

            await sub.stop()
        """
        await self.__aexit__(None, None, None)

    async def get_message(self) -> Message:
        """Wait for and return the next message from the subscription queue."""
        return await self._queue.get()

    def __aiter__(self) -> AsyncIterator[Message]:
        """Return self as the async iterator."""
        return self

    async def __anext__(self) -> Message:
        """Return the next message, suspending until one is available."""
        return await self.get_message()


class MQTTClient:
    """Asyncio MQTT client with automatic reconnection.

    Use as an async context manager. subscribe() returns a Subscription that
    is itself an async context manager.
    """

    def __init__(
        self,
        host: str,
        port: int = 1883,
        *,
        client_id: str = "",
        keepalive: int = 60,
        clean_session: bool = True,
        username: str | None = None,
        password: str | None = None,
        tls: ssl.SSLContext | bool | None = None,
        reconnect: ReconnectConfig | None = None,
        transport_factory: TransportFactory | None = None,
        version: Literal["3.1.1", "5.0"] = "3.1.1",
        session_expiry_interval: int = 0,
    ) -> None:
        """Create an MQTT client.

        The client must be used as an async context manager to establish the
        connection::

            async with MQTTClient("broker.example.com") as client:
                await client.publish("sensors/temp", "22.5")

        Prefer :func:`create_client` for version-typed access.

        Args:
            host: Broker hostname or IP address.
            port: TCP port. Defaults to ``1883`` (``8883`` is conventional for TLS).
            client_id: Client identifier sent in CONNECT. An empty string lets the
                broker assign one.
            keepalive: Keepalive interval in seconds. ``0`` disables keepalive.
            clean_session: Start with a clean session (MQTT 3.1.1) or discard any
                existing session state on connect.
            username: Optional username for broker authentication.
            password: Optional plain-text password for broker authentication.
            tls: TLS configuration. Pass ``True`` for default TLS, an
                :class:`ssl.SSLContext` for custom settings, or ``False`` (default)
                for a plain TCP connection.
            reconnect: Reconnection policy. Defaults to
                :class:`ReconnectConfig` with exponential back-off enabled.
            transport_factory: Override the low-level transport. Useful for testing.
            version: MQTT protocol version to use. Either ``"3.1.1"`` (default) or
                ``"5.0"``.
            session_expiry_interval: MQTT 5.0 session expiry interval in seconds.
                ``0`` means the session expires on disconnect.
        """
        self._host = host
        self._port = port
        self._client_id = client_id
        self._keepalive = keepalive
        self._clean_session = clean_session
        self._username = username
        self._password = password
        self._tls = tls
        self._reconnect = reconnect or ReconnectConfig()
        self._transport_factory: TransportFactory = transport_factory or _default_transport_factory
        self._version: Final = version
        self._session_expiry_interval = session_expiry_interval
        self._protocol: MQTTProtocol | None = None
        self._subscriptions: list[Subscription] = []
        self._run_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Self:
        """Connect to the broker and start the background run loop."""
        await self._connect_with_retry()
        self._run_task = asyncio.create_task(self._run_loop())
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Disconnect cleanly and cancel the run loop."""
        async with defer_cancellation():
            if self._run_task is not None:
                self._run_task.cancel()
                await asyncio.gather(self._run_task, return_exceptions=True)
                self._run_task = None
            if self._protocol is not None:
                await self._protocol.disconnect()
                self._protocol = None

    async def connect(self) -> None:
        """Connect to the broker and start the background run loop.

        Equivalent to entering the async context manager.
        Must be paired with a corresponding :meth:`disconnect` call to send
        DISCONNECT, close the socket, and stop the background run loop.

        Example::

            client = create_client("broker.example.com")
            await client.connect()
            # ... use the client
            await client.disconnect()

        Raises:
            MQTTConnectError: If the broker refuses the connection.
        """
        await self.__aenter__()

    async def disconnect(self) -> None:
        """Disconnect cleanly and stop the background run loop.

        Equivalent to exiting the async context manager. Sends DISCONNECT,
        closes the socket, and cancels the internal run loop task. Safe to
        call even if the connection has already been lost.
        """
        await self.__aexit__(None, None, None)

    async def publish(
        self,
        topic: str,
        payload: bytes | str,
        *,
        qos: QoS = QoS.AT_MOST_ONCE,
        retain: bool = False,
        properties: PublishProperties | None = None,
    ) -> None:
        """Publish a message to *topic*.

        Args:
            topic: Topic string. Must not contain wildcards.
            payload: Message body. ``str`` values are UTF-8 encoded automatically.
            qos: Delivery guarantee level. Defaults to ``AT_MOST_ONCE``.
            retain: Ask the broker to retain the message for future subscribers.
            properties: MQTT 5.0 publish properties. Raises if used with MQTT 3.1.1.

        Raises:
            MQTTInvalidTopicError: If *topic* is empty, contains wildcards, or has
                ``$`` in a non-leading position.
            MQTTDisconnectedError: If the client is not currently connected.
            RuntimeError: If *properties* is supplied on an MQTT 3.1.1 connection.
        """
        validate_publish(topic)
        if self._protocol is None:
            msg = "Not connected"
            raise MQTTDisconnectedError(msg)
        if properties is not None and self._version != "5.0":
            msg = "properties require MQTT 5.0"
            raise RuntimeError(msg)
        if isinstance(payload, str):
            payload = payload.encode()
        await self._protocol.publish(
            Publish(
                topic=topic,
                payload=payload,
                qos=qos,
                retain=retain,
                dup=False,
                properties=properties,
            ),
        )

    async def ping(self, timeout: float = 10.0) -> float:
        """Send a PINGREQ and return the round-trip time in seconds.

        Args:
            timeout: Seconds to wait for PINGRESP before raising
                :exc:`MQTTTimeoutError`.

        Returns:
            RTT in seconds.

        Raises:
            MQTTDisconnectedError: If the client is not currently connected.
            MQTTTimeoutError: If no PINGRESP is received within *timeout* seconds.
        """
        if self._protocol is None:
            msg = "Not connected"
            raise MQTTDisconnectedError(msg)
        return await self._protocol.ping(timeout=timeout)

    def subscribe(
        self,
        *filters: str,
        qos: QoS = QoS.AT_MOST_ONCE,
        auto_ack: bool = True,
        receive_buffer_size: int = 1000,
        no_local: bool = False,
        retain_as_published: bool = False,
    ) -> Subscription:
        """Create a :class:`Subscription` for one or more topic filters.

        The returned object must be used as an async context manager to activate
        the subscription and unsubscribe on exit::

            async with client.subscribe("sensors/#", qos=QoS.AT_LEAST_ONCE) as sub:
                async for msg in sub:
                    print(msg.topic, msg.payload)

        Args:
            *filters: One or more MQTT topic filters. Wildcards ``+`` (single level)
                and ``#`` (multi-level) are supported.
            qos: Maximum QoS level requested from the broker.
            auto_ack: Automatically send PUBACK/PUBREC upon receipt. Set to
                ``False`` to acknowledge manually via :meth:`Message.ack`.
            receive_buffer_size: Maximum messages buffered in the internal queue.
                Older messages are dropped when the queue is full.
            no_local: Do not receive messages published by this client (MQTT 5.0
                only).
            retain_as_published: Preserve the retain flag on forwarded messages
                (MQTT 5.0 only).

        Raises:
            MQTTInvalidTopicError: If any filter is empty, has ``$`` in a non-leading
                position, or contains a malformed wildcard (e.g. ``sensors#`` or
                ``a/b#/c``).
            RuntimeError: If *no_local* or *retain_as_published* are used on an
                MQTT 3.1.1 connection.
        """
        for f in filters:
            validate_subscribe_topic(f)
        if (no_local or retain_as_published) and self._version != "5.0":
            msg = "no_local and retain_as_published require MQTT 5.0"
            raise RuntimeError(msg)
        return Subscription(
            self,
            list(filters),
            qos,
            auto_ack,
            receive_buffer_size,
            no_local,
            retain_as_published,
        )

    async def request(
        self,
        topic: str,
        payload: bytes | str,
        *,
        qos: QoS = QoS.AT_MOST_ONCE,
        timeout: float = 30.0,
        properties: PublishProperties | None = None,
    ) -> Message:
        """Send a request and wait for exactly one reply (MQTT 5.0 only).

        Publishes *payload* to *topic* with a ``response_topic`` property, then
        waits for the first message that arrives on that topic and returns it.

        Both ``response_topic`` and ``correlation_data`` are taken from
        *properties* when set; otherwise they are generated automatically
        (a unique ``_zmqtt/reply/<32 hex chars>`` topic and 16 random bytes
        respectively).

        Args:
            topic: Request topic. Must not contain wildcards.
            payload: Request body. ``str`` values are UTF-8 encoded automatically.
            qos: QoS for the outgoing request publish.
            timeout: Seconds to wait for the reply before raising
                ``asyncio.TimeoutError``.
            properties: Publish properties for the request. ``response_topic``
                selects the reply topic (must not contain wildcards
                [MQTT-3.3.2-14]). ``correlation_data`` is forwarded as-is to
                the responder.

        Returns:
            The first ``Message`` received on the reply topic.

        Raises:
            RuntimeError: If the client is not using MQTT 5.0.
            MQTTInvalidTopicError: If ``properties.response_topic`` contains wildcards.
            MQTTDisconnectedError: If the connection is lost while waiting.
            asyncio.TimeoutError: If no reply arrives within *timeout* seconds.
        """
        if self._version != "5.0":
            msg = "request() requires MQTT 5.0"
            raise RuntimeError(msg)

        if properties is not None and properties.response_topic is not None:
            reply_topic = properties.response_topic
            validate_response_topic(reply_topic)
        else:
            reply_topic = f"_zmqtt/reply/{os.urandom(16).hex()}"

        if properties is not None and properties.correlation_data is not None:
            corr = properties.correlation_data
        else:
            corr = os.urandom(16)

        if properties is not None:
            req_props = dataclasses.replace(
                properties,
                response_topic=reply_topic,
                correlation_data=corr,
            )
        else:
            req_props = PublishProperties(
                response_topic=reply_topic,
                correlation_data=corr,
            )

        async with self.subscribe(reply_topic, receive_buffer_size=1) as sub:
            await self.publish(topic, payload, qos=qos, properties=req_props)
            return await asyncio.wait_for(sub.get_message(), timeout=timeout)

    async def auth(self, method: str, data: bytes | None = None) -> None:
        """Send an AUTH packet for enhanced authentication (MQTT 5.0 only).

        Args:
            method: Authentication method name negotiated with the broker.
            data: Optional authentication data to include in the packet.

        Raises:
            RuntimeError: If the client is not using MQTT 5.0.
            MQTTDisconnectedError: If the client is not currently connected.
        """
        if self._version != "5.0":
            msg = "AUTH requires MQTT 5.0"
            raise RuntimeError(msg)
        if self._protocol is None:
            msg = "Not connected"
            raise MQTTDisconnectedError(msg)

        props = AuthProperties(authentication_method=method, authentication_data=data)
        await self._protocol.send_auth(Auth(reason_code=0x18, properties=props))

    async def _connect(self) -> None:
        transport = await self._transport_factory(self._host, self._port, self._tls)
        protocol = MQTTProtocol(
            transport,
            SessionState(),
            keepalive=self._keepalive,
            version=self._version,
        )
        connect_props = None
        if self._version == "5.0":
            connect_props = ConnectProperties(
                session_expiry_interval=self._session_expiry_interval,
            )
        connect_packet = Connect(
            client_id=self._client_id,
            clean_session=self._clean_session,
            keepalive=self._keepalive,
            username=self._username,
            password=self._password.encode() if self._password is not None else None,
            properties=connect_props,
        )
        try:
            await protocol.connect(connect_packet)
        except BaseException:
            await transport.close()
            raise
        self._protocol = protocol

    async def _connect_with_retry(self) -> None:
        delay = self._reconnect.initial_delay
        attempt = 0
        while True:
            try:
                await self._connect()
            except MQTTConnectError:  # noqa: PERF203
                raise
            except OSError:
                attempt += 1
                max_a = self._reconnect.max_attempts
                if not self._reconnect.enabled or (max_a is not None and attempt >= max_a):
                    raise
                log.warning("Connection failed, retrying in %.1fs", delay, exc_info=True)
                await asyncio.sleep(delay)
                delay = min(delay * self._reconnect.backoff_factor, self._reconnect.max_delay)
            else:
                return

    async def _run_loop(self) -> None:
        subs_to_restore: list[Subscription] = []

        while True:
            if self._protocol is None:
                msg = "Not connected yet"
                raise RuntimeError(msg)
            protocol_run_task = asyncio.create_task(self._protocol.run())
            try:
                # Run the protocol as a sub-task so _read_loop is live while we
                # re-subscribe.  For the first connection subs_to_restore is empty,
                # so this collapses to the original "await protocol.run()" pattern.
                if subs_to_restore:
                    await self._protocol.started_event.wait()
                    for sub in subs_to_restore:
                        await sub._reconnect(self._protocol)
                    subs_to_restore = []
                await protocol_run_task

            except (MQTTDisconnectedError, MQTTTimeoutError):
                if not self._reconnect.enabled:
                    raise
                # Close the dead transport to release the file descriptor.
                with contextlib.suppress(Exception):
                    await self._protocol._transport.close()
            else:
                return  # clean disconnect — protocol.disconnect() was called

            subs_to_restore = list(self._subscriptions)
            log.warning("Connection lost, reconnecting...")
            await self._connect_with_retry()
            log.info("Successfully reconnected")


@overload
def create_client(
    host: str,
    port: int = ...,
    *,
    client_id: str = ...,
    keepalive: int = ...,
    clean_session: bool = ...,
    username: str | None = ...,
    password: str | None = ...,
    tls: ssl.SSLContext | bool = ...,
    reconnect: ReconnectConfig | None = ...,
    transport_factory: TransportFactory | None = ...,
    session_expiry_interval: int = ...,
    version: Literal["3.1.1"],
) -> MQTTClientV311: ...


@overload
def create_client(
    host: str,
    port: int = ...,
    *,
    client_id: str = ...,
    keepalive: int = ...,
    clean_session: bool = ...,
    username: str | None = ...,
    password: str | None = ...,
    tls: ssl.SSLContext | bool = ...,
    reconnect: ReconnectConfig | None = ...,
    transport_factory: TransportFactory | None = ...,
    session_expiry_interval: int = ...,
    version: Literal["5.0"],
) -> MQTTClientV5: ...


def create_client(
    host: str,
    port: int = 1883,
    *,
    client_id: str = "",
    keepalive: int = 60,
    clean_session: bool = True,
    username: str | None = None,
    password: str | None = None,
    tls: ssl.SSLContext | bool = False,
    reconnect: ReconnectConfig | None = None,
    transport_factory: TransportFactory | None = None,
    session_expiry_interval: int = 0,
    version: Literal["3.1.1", "5.0"] = "3.1.1",
) -> MQTTClientV311 | MQTTClientV5:
    """Create a version-typed MQTT client.

    Returns MQTTClientV311 when version="3.1.1", MQTTClientV5 when version="5.0".
    The concrete type is always MQTTClient; the return type is a Protocol view.
    """
    return MQTTClient(
        host,
        port,
        client_id=client_id,
        keepalive=keepalive,
        clean_session=clean_session,
        username=username,
        password=password,
        tls=tls,
        reconnect=reconnect,
        transport_factory=transport_factory,
        version=version,
        session_expiry_interval=session_expiry_interval,
    )
