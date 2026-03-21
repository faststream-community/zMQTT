"""High-level MQTT client — public API layer."""

import asyncio
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import AsyncIterator, Final, Literal, Protocol, overload

from zmqtt.errors import MQTTDisconnectedError, MQTTTimeoutError
from zmqtt.log import get_logger
from zmqtt.packets.auth import Auth
from zmqtt.packets.connect import Connect
from zmqtt.packets.properties import (
    AuthProperties,
    ConnectProperties,
    PublishProperties,
)
from zmqtt.packets.publish import Publish
from zmqtt.packets.subscribe import SubscriptionRequest
from zmqtt.protocol import MQTTProtocol
from zmqtt.state import SessionState
from zmqtt.transport.base import Transport
from zmqtt.transport.tcp import open_tcp
from zmqtt.transport.tls import open_tls
from zmqtt.types import Message, QoS

TransportFactory = Callable[[str, int, ssl.SSLContext | bool], Awaitable[Transport]]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReconnectConfig:
    enabled: bool = True
    initial_delay: float = 1.0
    max_delay: float = 60.0
    backoff_factor: float = 2.0


async def _default_transport_factory(
    host: str, port: int, tls: ssl.SSLContext | bool
) -> Transport:
    if tls is False:
        return await open_tcp(host, port)
    if tls is True:
        return await open_tls(host, port)
    return await open_tls(host, port, tls)


class MQTTClientV311(Protocol):
    """Type-safe view of MQTTClient for MQTT 3.1.1 connections."""

    async def __aenter__(self) -> "MQTTClientV311": ...
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
        receive_buffer_size: int = 0,
    ) -> "Subscription": ...

    async def ping(self, timeout: float = 10.0) -> float: ...


class MQTTClientV5(Protocol):
    """Type-safe view of MQTTClient for MQTT 5.0 connections."""

    async def __aenter__(self) -> "MQTTClientV5": ...
    async def __aexit__(self, *exc: object) -> None: ...

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
        receive_buffer_size: int = 0,
        no_local: bool = False,
        retain_as_published: bool = False,
    ) -> "Subscription": ...

    async def auth(self, method: str, data: bytes | None = None) -> None: ...

    async def ping(self, timeout: float = 10.0) -> float: ...


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
        receive_buffer_size: int = 0,
        no_local: bool = False,
        retain_as_published: bool = False,
    ) -> None:
        self._client = client
        self._filters = filters
        self._qos = qos
        self._auto_ack = auto_ack
        self._queue: asyncio.Queue[Message] = asyncio.Queue(receive_buffer_size or 1000)
        self._relay_tasks: list[asyncio.Task[None]] = []
        self._no_local = no_local
        self._retain_as_published = retain_as_published
        self._registered_filters: list[str] = []

    async def __aenter__(self) -> "Subscription":
        if self._client._protocol is None:
            raise MQTTDisconnectedError("Not connected")
        self._client._subscriptions.append(self)
        await self._do_subscribe(self._client._protocol)
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._client._subscriptions.remove(self)
        await self._cancel_relays()
        task = asyncio.current_task()
        being_cancelled = task is not None and task.cancelling() > 0
        if (
            not being_cancelled
            and self._registered_filters
            and self._client._protocol is not None
        ):
            try:
                await self._client._protocol.unsubscribe(self._registered_filters)
            except Exception:
                pass

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

    async def get_message(self) -> Message:
        return await self._queue.get()

    def __aiter__(self) -> AsyncIterator[Message]:
        return self

    async def __anext__(self) -> Message:
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
        tls: ssl.SSLContext | bool = False,
        reconnect: ReconnectConfig | None = None,
        transport_factory: TransportFactory | None = None,
        version: Literal["3.1.1", "5.0"] = "3.1.1",
        session_expiry_interval: int = 0,
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._keepalive = keepalive
        self._clean_session = clean_session
        self._username = username
        self._password = password
        self._tls = tls
        self._reconnect = reconnect or ReconnectConfig()
        self._transport_factory: TransportFactory = (
            transport_factory or _default_transport_factory
        )
        self._version: Final = version
        self._session_expiry_interval = session_expiry_interval
        self._protocol: MQTTProtocol | None = None
        self._subscriptions: list[Subscription] = []
        self._run_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "MQTTClient":
        await self._connect()
        self._run_task = asyncio.create_task(self._run_loop())
        return self

    async def __aexit__(self, *exc: object) -> None:
        task = asyncio.current_task()
        cancels = task.cancelling() if task else 0
        for _ in range(cancels):
            task.uncancel()  # type: ignore[union-attr]
        try:
            if self._run_task is not None:
                self._run_task.cancel()
                await asyncio.gather(self._run_task, return_exceptions=True)
                self._run_task = None
            if self._protocol is not None:
                await self._protocol.disconnect()
                self._protocol = None
        finally:
            for _ in range(cancels):
                task.cancel()  # type: ignore[union-attr]

    async def publish(
        self,
        topic: str,
        payload: bytes | str,
        *,
        qos: QoS = QoS.AT_MOST_ONCE,
        retain: bool = False,
        properties: PublishProperties | None = None,
    ) -> None:
        if self._protocol is None:
            raise MQTTDisconnectedError("Not connected")
        if properties is not None and self._version != "5.0":
            raise RuntimeError("properties require MQTT 5.0")
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
            )
        )

    async def ping(self, timeout: float = 10.0) -> float:
        """Send PINGREQ and return RTT in seconds when PINGRESP is received."""
        if self._protocol is None:
            raise MQTTDisconnectedError("Not connected")
        return await self._protocol.ping(timeout=timeout)

    def subscribe(
        self,
        *filters: str,
        qos: QoS = QoS.AT_MOST_ONCE,
        auto_ack: bool = True,
        receive_buffer_size: int = 0,
        no_local: bool = False,
        retain_as_published: bool = False,
    ) -> Subscription:
        if (no_local or retain_as_published) and self._version != "5.0":
            raise RuntimeError("no_local and retain_as_published require MQTT 5.0")
        return Subscription(
            self,
            list(filters),
            qos,
            auto_ack,
            receive_buffer_size,
            no_local,
            retain_as_published,
        )

    async def auth(self, method: str, data: bytes | None = None) -> None:
        """Send AUTH packet for enhanced authentication (MQTT 5.0 only)."""
        if self._version != "5.0":
            raise RuntimeError("AUTH requires MQTT 5.0")
        if self._protocol is None:
            raise MQTTDisconnectedError("Not connected")

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
                session_expiry_interval=self._session_expiry_interval
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

    async def _run_loop(self) -> None:
        delay = self._reconnect.initial_delay
        subs_to_restore: list[Subscription] = []

        while True:
            assert self._protocol is not None
            try:
                # Run the protocol as a sub-task so _read_loop is live while we
                # re-subscribe.  For the first connection subs_to_restore is empty,
                # so this collapses to the original "await protocol.run()" pattern.
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(self._protocol.run())
                    if subs_to_restore:
                        await asyncio.sleep(0)  # let _read_loop start
                        for sub in subs_to_restore:
                            await sub._reconnect(self._protocol)
                        subs_to_restore = []
            except* (MQTTDisconnectedError, MQTTTimeoutError):
                if not self._reconnect.enabled:
                    raise
                # Close the dead transport to release the file descriptor.
                try:
                    await self._protocol._transport.close()
                except Exception:
                    pass
            else:
                return  # clean disconnect — protocol.disconnect() was called

            subs_to_restore = list(self._subscriptions)

            while True:
                log.warning("Connection lost, reconnecting in %.1fs", delay)
                await asyncio.sleep(delay)
                try:
                    await self._connect()
                    delay = self._reconnect.initial_delay
                    log.info("Successfully reconnected")
                    break
                except Exception:
                    log.warning("Reconnect failed", exc_info=True)
                    if self._protocol is not None:
                        try:
                            await self._protocol.disconnect()
                        except Exception:
                            pass
                        self._protocol = None
                    delay = min(
                        delay * self._reconnect.backoff_factor,
                        self._reconnect.max_delay,
                    )


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
