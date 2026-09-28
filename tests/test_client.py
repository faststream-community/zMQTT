"""Unit tests for MQTTClient construction and connect-retry behaviour."""

import asyncio
import ssl
from collections import deque
from typing import Literal

import pytest

from zmqtt import (
    MQTTClient,
    MQTTDisconnectedError,
    MQTTTimeoutError,
    QoS,
    ReconnectConfig,
    Will,
    WillProperties,
    create_client,
)
from zmqtt._internal.packets.codec import encode
from zmqtt._internal.packets.connect import ConnAck
from zmqtt._internal.packets.ping import PingReq, PingResp
from zmqtt._internal.packets.publish import Publish
from zmqtt._internal.packets.types import PacketType
from zmqtt._internal.subscription_index import SubscriptionEntry
from zmqtt._internal.transport.base import Transport


def test_mqtt_connect_timeout_default_is_30s() -> None:
    client = MQTTClient("localhost")
    assert client._mqtt_connect_timeout == 30.0


@pytest.mark.parametrize("bad", [0, -1, -0.5, float("nan")])
def test_non_positive_mqtt_connect_timeout_raises(bad: float) -> None:
    with pytest.raises(ValueError, match="mqtt_connect_timeout must be positive"):
        create_client("localhost", mqtt_connect_timeout=bad)


def test_create_client_accepts_will() -> None:
    will = Will(
        topic="status/client",
        payload=b"offline",
        qos=QoS.AT_LEAST_ONCE,
        retain=True,
    )

    client = create_client("localhost", will=will)

    assert isinstance(client, MQTTClient)


def test_mqtt_v311_rejects_will_properties() -> None:
    will = Will(
        topic="status/client",
        payload=b"offline",
        qos=QoS.AT_LEAST_ONCE,
        retain=True,
        properties=WillProperties(content_type="text/plain"),
    )

    with pytest.raises(RuntimeError, match=r"will properties require MQTT 5\.0"):
        MQTTClient("localhost", version="3.1.1", will=will)


class FakeTransport:
    """Minimal Transport: read() hangs until fed bytes or an error; tracks close()."""

    def __init__(self, feed: bytes | None = None) -> None:
        self.sent: list[bytes] = []
        self.closed = False
        self._rx: deque[bytes | Exception] = deque()
        if feed is not None:
            self._rx.append(feed)

    async def read(self, n: int) -> bytes:  # noqa: ARG002
        while not self._rx:  # noqa: ASYNC110
            await asyncio.sleep(0)
        item = self._rx.popleft()
        if isinstance(item, Exception):
            raise item
        return item

    async def write(self, data: bytes) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True

    @property
    def is_connected(self) -> bool:
        return not self.closed


class LiveBrokerTransport(FakeTransport):
    """FakeTransport that answers every PINGREQ and signals when CONNECT is sent."""

    def __init__(self, feed: bytes | None = None) -> None:
        super().__init__(feed)
        self.connect_sent = asyncio.Event()

    async def write(self, data: bytes) -> None:
        await super().write(data)
        if data[0] >> 4 == PacketType.CONNECT:
            self.connect_sent.set()
        elif data == encode(PingReq(), version="3.1.1"):
            self._rx.append(encode(PingResp(), version="3.1.1"))


async def test_connect_retries_after_connack_timeout() -> None:
    connack = encode(ConnAck(session_present=False, return_code=0), version="3.1.1")
    transports = [
        FakeTransport(),  # 1st attempt: never CONNACKs -> times out
        FakeTransport(feed=connack),  # 2nd attempt: succeeds
    ]
    made: list[FakeTransport] = []

    async def factory(host: str, port: int, tls: ssl.SSLContext | bool | None) -> Transport:  # noqa: ARG001
        transport = transports[len(made)]
        made.append(transport)
        return transport

    client = MQTTClient(
        "localhost",
        mqtt_connect_timeout=0.05,
        reconnect=ReconnectConfig(initial_delay=0.0, max_attempts=None),
        transport_factory=factory,
    )

    await client._connect_with_retry()

    assert len(made) == 2  # the first (timed-out) attempt was retried
    assert made[0].closed  # the dead transport's fd was released
    assert client._protocol is not None


async def test_mqtt_connect_timeout_gives_up_after_max_attempts() -> None:
    made: list[FakeTransport] = []

    async def factory(host: str, port: int, tls: ssl.SSLContext | bool | None) -> Transport:  # noqa: ARG001
        transport = FakeTransport()  # never fed -> CONNACK never arrives -> times out
        made.append(transport)
        return transport

    client = MQTTClient(
        "localhost",
        mqtt_connect_timeout=0.05,
        reconnect=ReconnectConfig(initial_delay=0.0, max_attempts=1),
        transport_factory=factory,
    )

    with pytest.raises(MQTTTimeoutError):
        await client._connect_with_retry()

    assert len(made) == 1  # gave up after the single allowed attempt
    assert made[0].closed  # transport still cleaned up on give-up


async def test_run_loop_reconnects_after_transport_oserror() -> None:
    connack = encode(ConnAck(session_present=False, return_code=0), version="3.1.1")
    first = FakeTransport(feed=connack)
    retry = FakeTransport(feed=connack)
    made: list[FakeTransport] = []

    async def factory(host: str, port: int, tls: ssl.SSLContext | bool | None) -> Transport:  # noqa: ARG001
        transport = (first, retry)[len(made)]
        made.append(transport)
        return transport

    client = MQTTClient(
        "localhost",
        reconnect=ReconnectConfig(initial_delay=0.0, max_attempts=None),
        transport_factory=factory,
    )

    async with client:
        assert client.connection_info.connection_id == 1
        first._rx.append(OSError("connection reset"))

        async def reconnected() -> None:
            while True:
                await asyncio.sleep(0)
                info = client._connection_info
                if info is not None and info.connection_id == 2:
                    return

        await asyncio.wait_for(reconnected(), timeout=2)

        assert client._run_task is not None
        assert not client._run_task.done()
        assert len(made) == 2
        assert first.closed
        assert retry.is_connected
        assert client.connection_info.connection_id == 2


async def test_disconnect_returns_during_reconnect_handshake() -> None:
    # Before Python 3.12, asyncio.wait_for() returned CONNACK instead of raising
    # the cancellation from disconnect(), which then waited for a healthy run loop.
    connack = encode(ConnAck(session_present=False, return_code=0), version="3.1.1")
    first = LiveBrokerTransport(feed=connack)
    retry = LiveBrokerTransport(feed=connack)
    transports = iter((first, retry))

    async def factory(host: str, port: int, tls: ssl.SSLContext | bool | None) -> Transport:  # noqa: ARG001
        return next(transports)

    client = MQTTClient(
        "localhost",
        keepalive=1,
        reconnect=ReconnectConfig(initial_delay=0.0),
        transport_factory=factory,
    )
    await client.connect()
    first._rx.append(OSError("connection reset"))
    await retry.connect_sent.wait()

    await asyncio.wait_for(client.disconnect(), timeout=3)

    assert client._run_task is None
    assert retry.closed


async def test_detach_rejects_new_reads() -> None:
    client = MQTTClient("localhost")
    subscription = client.subscribe("events")
    client._subscriptions.append(subscription)

    await subscription.detach()

    with pytest.raises(MQTTDisconnectedError, match="Subscription detached"):
        await subscription.get_message()


@pytest.mark.parametrize("version", ["3.1.1", "5.0"])
@pytest.mark.parametrize("qos", [QoS.AT_LEAST_ONCE, QoS.EXACTLY_ONCE])
@pytest.mark.parametrize("during_handshake", [False, True])
async def test_detach_survives_reconnect_before_first_publish(
    version: Literal["3.1.1", "5.0"],
    qos: QoS,
    during_handshake: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = LiveBrokerTransport(feed=encode(ConnAck(session_present=False, return_code=0), version=version))
    retry = LiveBrokerTransport()
    transports = iter((first, retry))

    async def factory(host: str, port: int, tls: ssl.SSLContext | bool | None) -> Transport:  # noqa: ARG001
        return next(transports)

    client = MQTTClient(
        "localhost",
        version=version,
        clean_session=False,
        session_expiry_interval=60 if version == "5.0" else 0,
        reconnect=ReconnectConfig(initial_delay=0),
        transport_factory=factory,
        session_replay_timeout=0.01,
    )
    reconnected = asyncio.Event()
    connect = client._connect

    async def observe_connect() -> None:
        await connect()
        if client.connection_info.connection_id == 2:
            reconnected.set()

    monkeypatch.setattr(client, "_connect", observe_connect)
    async with client:
        subscription = client.subscribe("t/#", qos=qos, auto_ack=False)
        subscription._registered_filters = ["t/#"]
        client._subscriptions.append(subscription)
        assert client._protocol is not None
        client._protocol._state.subscriptions.add(
            "t/#",
            SubscriptionEntry(queue=subscription._queue, actual_filter="t/#", auto_ack=False),
        )
        if not during_handshake:
            await subscription.detach()
        first._rx.append(OSError("connection reset"))
        await asyncio.wait_for(retry.connect_sent.wait(), timeout=1)
        if during_handshake:
            # The subscription has already been captured in subs_to_restore.
            await subscription.detach()

        def publish(packet_id: int) -> bytes:
            return encode(
                Publish(topic="t/x", payload=b"unprocessed", qos=qos, retain=False, dup=False, packet_id=packet_id),
                version=version,
            )

        retry._rx.append(encode(ConnAck(session_present=True, return_code=0), version=version) + publish(1))
        await asyncio.wait_for(reconnected.wait(), timeout=1)
        await client.ping(timeout=1)
        await asyncio.sleep(0.03)
        retry._rx.append(publish(2))
        await client.ping(timeout=1)

        # Neither a new SUBSCRIBE nor PUBACK/PUBREC may be sent, even for
        # PUBLISH buffered with CONNACK or arriving after the replay timeout.
        assert {data[0] >> 4 for data in retry.sent} == {PacketType.CONNECT, PacketType.PINGREQ}
        assert subscription._queue.empty()
