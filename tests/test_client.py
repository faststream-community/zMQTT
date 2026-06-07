"""Unit tests for MQTTClient construction and connect-retry behaviour."""

import asyncio
import ssl
from collections import deque

import pytest

from zmqtt import MQTTClient, MQTTTimeoutError, ReconnectConfig, create_client
from zmqtt._internal.packets.codec import encode
from zmqtt._internal.packets.connect import ConnAck
from zmqtt._internal.transport.base import Transport


def test_connect_timeout_default_is_30s() -> None:
    client = MQTTClient("localhost")
    assert client._connect_timeout == 30.0


@pytest.mark.parametrize("bad", [0, -1, -0.5, float("nan")])
def test_non_positive_connect_timeout_raises(bad: float) -> None:
    with pytest.raises(ValueError, match="connect_timeout must be positive"):
        create_client("localhost", connect_timeout=bad)


class FakeTransport:
    """Minimal Transport: read() hangs until fed; tracks close()."""

    def __init__(self, feed: bytes | None = None) -> None:
        self.sent: list[bytes] = []
        self.closed = False
        self._rx: deque[bytes] = deque()
        if feed is not None:
            self._rx.append(feed)

    async def read(self, n: int) -> bytes:  # noqa: ARG002
        while not self._rx:  # noqa: ASYNC110
            await asyncio.sleep(0)
        return self._rx.popleft()

    async def write(self, data: bytes) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True

    @property
    def is_connected(self) -> bool:
        return not self.closed


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
        connect_timeout=0.05,
        reconnect=ReconnectConfig(initial_delay=0.0, max_attempts=None),
        transport_factory=factory,
    )

    await client._connect_with_retry()

    assert len(made) == 2  # the first (timed-out) attempt was retried
    assert made[0].closed  # the dead transport's fd was released
    assert client._protocol is not None


async def test_connect_timeout_gives_up_after_max_attempts() -> None:
    made: list[FakeTransport] = []

    async def factory(host: str, port: int, tls: ssl.SSLContext | bool | None) -> Transport:  # noqa: ARG001
        transport = FakeTransport()  # never fed -> CONNACK never arrives -> times out
        made.append(transport)
        return transport

    client = MQTTClient(
        "localhost",
        connect_timeout=0.05,
        reconnect=ReconnectConfig(initial_delay=0.0, max_attempts=1),
        transport_factory=factory,
    )

    with pytest.raises(MQTTTimeoutError):
        await client._connect_with_retry()

    assert len(made) == 1  # gave up after the single allowed attempt
    assert made[0].closed  # transport still cleaned up on give-up
