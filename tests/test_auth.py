import asyncio
from collections import deque

import pytest

from zmqtt import MQTTClient, ReconnectConfig
from zmqtt._internal.packets.auth import Auth
from zmqtt._internal.packets.codec import AnyPacket, encode
from zmqtt._internal.packets.connect import ConnAck, Connect
from zmqtt._internal.packets.disconnect import Disconnect
from zmqtt._internal.packets.properties import (
    AuthProperties,
    ConnAckProperties,
    ConnectProperties,
    DisconnectProperties,
)
from zmqtt._internal.packets.reader import PacketBuffer
from zmqtt._internal.protocol import MQTTProtocol
from zmqtt._internal.transport.base import Transport
from zmqtt.errors import (
    MQTTAuthError,
    MQTTConnectError,
    MQTTDisconnectedError,
    MQTTProtocolError,
    MQTTTimeoutError,
)

from .test_protocol import FakeTransport, _answer_after, _run_read_loop, _stop_task, make_protocol


class FakeAuthHandler:
    def __init__(self, method: str = "TEST", responses: list[bytes | None] | None = None) -> None:
        self.method = method
        self._responses: deque[bytes | None] = deque(responses if responses is not None else [None])
        self.initial_calls = 0
        self.continue_calls: list[bytes | None] = []
        self.finalize_calls: list[bytes | None] = []
        self.finalize_error: Exception | None = None

    async def initial_data(self) -> bytes | None:
        self.initial_calls += 1
        return self._responses.popleft()

    async def continue_data(self, data: bytes | None) -> bytes | None:
        self.continue_calls.append(data)
        return self._responses.popleft()

    async def finalize_data(self, data: bytes | None) -> None:
        self.finalize_calls.append(data)
        if self.finalize_error is not None:
            raise self.finalize_error


def _connack(method: str | None = "TEST", data: bytes | None = None) -> bytes:
    props = ConnAckProperties(authentication_method=method, authentication_data=data) if method is not None else None
    return encode(ConnAck(session_present=False, return_code=0, properties=props), version="5.0")


def _auth_packet(reason_code: int, data: bytes | None = None, method: str | None = "TEST") -> Auth:
    return Auth(
        reason_code=reason_code,
        properties=AuthProperties(authentication_method=method, authentication_data=data),
    )


def _auth(reason_code: int, data: bytes | None = None, method: str | None = "TEST") -> bytes:
    return encode(_auth_packet(reason_code, data, method), version="5.0")


def _decode(data: bytes) -> AnyPacket:
    buf = PacketBuffer(version="5.0")
    buf.feed(data)
    (packet,) = list(buf)
    return packet


async def _connected(handler: FakeAuthHandler | None = None) -> tuple[MQTTProtocol, FakeTransport]:
    protocol, transport = make_protocol(version="5.0", auth_handler=handler)
    transport.feed(_connack(handler.method if handler is not None else None))
    await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))
    transport.sent.clear()
    return protocol, transport


async def test_connect_single_challenge_round_completes() -> None:
    handler = FakeAuthHandler(method="TEST", responses=[b"resp1"])
    protocol, transport = make_protocol(version="5.0", auth_handler=handler)
    transport.feed(_auth(0x18, b"challenge1"))
    transport.feed(_connack())

    ack = await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))

    assert ack.return_code == 0
    assert handler.continue_calls == [b"challenge1"]
    response = _decode(transport.sent[1])
    assert isinstance(response, Auth)
    assert response.reason_code == 0x18
    assert response.properties is not None
    assert response.properties.authentication_method == "TEST"
    assert response.properties.authentication_data == b"resp1"


async def test_connect_multiple_challenge_rounds_completes() -> None:
    handler = FakeAuthHandler(responses=[b"resp1", b"resp2"])
    protocol, transport = make_protocol(version="5.0", auth_handler=handler)
    transport.feed(_auth(0x18, b"c1"))
    transport.feed(_auth(0x18, b"c2"))
    transport.feed(_connack())

    ack = await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))

    assert ack.return_code == 0
    assert handler.continue_calls == [b"c1", b"c2"]
    assert len(transport.sent) == 3


async def test_connect_auth_unexpected_reason_code_raises() -> None:
    handler = FakeAuthHandler()
    protocol, transport = make_protocol(version="5.0", auth_handler=handler)
    transport.feed(encode(Auth(reason_code=0x00), version="5.0"))

    with pytest.raises(MQTTProtocolError):
        await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))


async def test_connect_auth_without_handler_raises() -> None:
    protocol, transport = make_protocol(version="5.0")
    transport.feed(encode(Auth(reason_code=0x18), version="5.0"))

    with pytest.raises(MQTTProtocolError):
        await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))


async def test_connect_refused_after_auth_exchange_raises() -> None:
    handler = FakeAuthHandler(responses=[b"resp1"])
    protocol, transport = make_protocol(version="5.0", auth_handler=handler)
    transport.feed(_auth(0x18, b"c1"))
    transport.feed(encode(ConnAck(session_present=False, return_code=0x87), version="5.0"))

    with pytest.raises(MQTTConnectError) as exc_info:
        await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))

    assert exc_info.value.return_code == 0x87


async def test_successful_connect_stores_negotiated_auth_method() -> None:
    handler = FakeAuthHandler(method="SCRAM-SHA-256")
    protocol, transport = make_protocol(version="5.0", auth_handler=handler)
    transport.feed(_connack("SCRAM-SHA-256"))

    await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))

    assert protocol._state.auth_method == "SCRAM-SHA-256"


async def test_connect_without_handler_leaves_auth_method_unset() -> None:
    protocol, transport = make_protocol(version="5.0")
    transport.feed(_connack(None))

    await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))

    assert protocol._state.auth_method is None


@pytest.mark.parametrize("method", [None, "WRONG"])
async def test_connect_success_with_wrong_auth_method_raises(method: str | None) -> None:
    handler = FakeAuthHandler(method="TEST")
    protocol, transport = make_protocol(version="5.0", auth_handler=handler)
    transport.feed(_connack(method))

    with pytest.raises(MQTTProtocolError, match="Authentication Method"):
        await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))

    assert protocol._state.auth_method is None
    assert handler.finalize_calls == []


@pytest.mark.parametrize("method", [None, "WRONG"])
async def test_connect_challenge_with_wrong_auth_method_raises(method: str | None) -> None:
    handler = FakeAuthHandler(method="TEST", responses=[b"resp1"])
    protocol, transport = make_protocol(version="5.0", auth_handler=handler)
    transport.feed(_auth(0x18, b"challenge1", method))

    with pytest.raises(MQTTProtocolError, match="Authentication Method"):
        await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))

    assert handler.continue_calls == []


@pytest.mark.parametrize("reason_code", [0x00, 0x18])
@pytest.mark.parametrize("method", [None, "WRONG"])
async def test_reauthenticate_response_with_wrong_auth_method_fails_exchange(
    reason_code: int,
    method: str | None,
) -> None:
    handler = FakeAuthHandler(method="TEST", responses=[b"resp1"])
    protocol, transport = await _connected(handler)
    handler.finalize_calls.clear()
    run_task = asyncio.create_task(protocol.run())
    await protocol.started_event.wait()
    reauth_task = asyncio.create_task(protocol.reauthenticate())

    await _answer_after(transport, sent=1, packet=_auth_packet(reason_code, b"data", method))

    with pytest.raises(MQTTProtocolError, match="Authentication Method"):
        await run_task
    with pytest.raises(MQTTDisconnectedError):
        await reauth_task
    assert handler.continue_calls == []
    assert handler.finalize_calls == []


async def test_connect_success_passes_final_data_to_finalize() -> None:
    handler = FakeAuthHandler()
    protocol, transport = make_protocol(version="5.0", auth_handler=handler)
    transport.feed(_connack(data=b"server-final"))

    await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))

    assert handler.finalize_calls == [b"server-final"]


async def test_connect_success_without_data_finalizes_with_none() -> None:
    handler = FakeAuthHandler()
    protocol, transport = make_protocol(version="5.0", auth_handler=handler)
    transport.feed(_connack())

    await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))

    assert handler.finalize_calls == [None]


async def test_connect_finalize_runs_after_challenge_rounds() -> None:
    order: list[str] = []

    class OrderHandler(FakeAuthHandler):
        async def continue_data(self, data: bytes | None) -> bytes | None:
            order.append("continue")
            return await super().continue_data(data)

        async def finalize_data(self, data: bytes | None) -> None:
            order.append("finalize")
            await super().finalize_data(data)

    handler = OrderHandler(responses=[b"resp1"])
    protocol, transport = make_protocol(version="5.0", auth_handler=handler)
    transport.feed(_auth(0x18, b"c1"))
    transport.feed(_connack(data=b"final"))

    await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))

    assert order == ["continue", "finalize"]
    assert handler.finalize_calls == [b"final"]


async def test_connect_finalize_error_rejects_connection() -> None:
    handler = FakeAuthHandler()
    handler.finalize_error = ValueError("bad server signature")
    protocol, transport = make_protocol(version="5.0", auth_handler=handler)
    transport.feed(_connack(data=b"forged"))

    with pytest.raises(ValueError, match="bad server signature"):
        await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))

    assert protocol._state.auth_method is None


async def test_reauthenticate_passes_final_data_to_finalize() -> None:
    handler = FakeAuthHandler()
    protocol, transport = await _connected(handler)
    handler.finalize_calls.clear()
    read_task = await _run_read_loop(protocol)

    reauth_task = asyncio.create_task(protocol.reauthenticate())
    await _answer_after(transport, sent=1, packet=_auth_packet(0x00, b"server-final"))
    await reauth_task

    assert handler.finalize_calls == [b"server-final"]

    await _stop_task(read_task)


async def test_reauthenticate_finalize_error_is_raised_to_caller() -> None:
    handler = FakeAuthHandler()
    protocol, transport = await _connected(handler)
    handler.finalize_error = ValueError("bad server signature")
    run_task = asyncio.create_task(protocol.run())
    await protocol.started_event.wait()

    reauth_task = asyncio.create_task(protocol.reauthenticate())
    await _answer_after(transport, sent=1, packet=_auth_packet(0x00, b"forged"))

    with pytest.raises(ValueError, match="bad server signature"):
        await run_task
    with pytest.raises(ValueError, match="bad server signature"):
        await reauth_task
    assert protocol._state.pending_auth is None


async def test_reauthenticate_direct_success() -> None:
    handler = FakeAuthHandler(method="TEST")
    protocol, transport = await _connected(handler)
    read_task = await _run_read_loop(protocol)

    reauth_task = asyncio.create_task(protocol.reauthenticate(b"start"))
    await _answer_after(transport, sent=1, packet=_auth_packet(0x00))
    await reauth_task

    assert protocol._state.pending_auth is None
    sent = _decode(transport.sent[0])
    assert isinstance(sent, Auth)
    assert sent.reason_code == 0x19
    assert sent.properties is not None
    assert sent.properties.authentication_method == "TEST"
    assert sent.properties.authentication_data == b"start"

    await _stop_task(read_task)


async def test_reauthenticate_with_challenge_round() -> None:
    handler = FakeAuthHandler(method="TEST", responses=[b"resp1"])
    protocol, transport = await _connected(handler)
    read_task = await _run_read_loop(protocol)

    reauth_task = asyncio.create_task(protocol.reauthenticate())
    await _answer_after(
        transport,
        sent=1,
        packet=_auth_packet(0x18, b"challenge"),
    )
    await _answer_after(transport, sent=2, packet=_auth_packet(0x00))
    await reauth_task

    assert handler.continue_calls == [b"challenge"]
    response = _decode(transport.sent[1])
    assert isinstance(response, Auth)
    assert response.reason_code == 0x18
    assert response.properties is not None
    assert response.properties.authentication_data == b"resp1"

    await _stop_task(read_task)


async def test_reauthenticate_without_negotiated_method_raises() -> None:
    protocol, _transport = await _connected()

    with pytest.raises(RuntimeError):
        await protocol.reauthenticate()


async def test_reauthenticate_concurrent_call_raises() -> None:
    handler = FakeAuthHandler()
    protocol, transport = await _connected(handler)
    read_task = await _run_read_loop(protocol)

    first = asyncio.create_task(protocol.reauthenticate())
    await asyncio.sleep(0)

    with pytest.raises(RuntimeError):
        await protocol.reauthenticate()

    await _answer_after(transport, sent=1, packet=_auth_packet(0x00))
    await first
    await _stop_task(read_task)


async def test_reauthenticate_timeout_aborts_connection() -> None:
    handler = FakeAuthHandler()
    protocol, transport = await _connected(handler)
    read_task = await _run_read_loop(protocol)

    with pytest.raises(MQTTTimeoutError):
        await protocol.reauthenticate(timeout=0.05)

    assert protocol._state.pending_auth is None
    assert not transport.is_connected

    await _stop_task(read_task)


async def test_reauthenticate_cancelled_aborts_connection() -> None:
    handler = FakeAuthHandler()
    protocol, transport = await _connected(handler)
    read_task = await _run_read_loop(protocol)

    reauth_task = asyncio.create_task(protocol.reauthenticate())
    await asyncio.sleep(0)
    reauth_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reauth_task

    assert protocol._state.pending_auth is None
    assert not transport.is_connected
    sent_before = len(transport.sent)

    with pytest.raises(MQTTDisconnectedError):
        await protocol.reauthenticate()
    assert len(transport.sent) == sent_before

    await _stop_task(read_task)


def _block_writes(transport: FakeTransport) -> asyncio.Event:
    """Make ``transport.write`` hang until the returned event is set (a stalled send)."""
    release = asyncio.Event()

    async def write(data: bytes) -> None:
        await release.wait()
        transport.sent.append(data)

    transport.write = write  # type: ignore[method-assign]
    return release


async def test_reauthenticate_cancelled_during_send_aborts_connection() -> None:
    handler = FakeAuthHandler()
    protocol, transport = await _connected(handler)
    read_task = await _run_read_loop(protocol)
    _block_writes(transport)
    reauth_task = asyncio.create_task(protocol.reauthenticate())
    await asyncio.sleep(0)

    reauth_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reauth_task

    assert protocol._state.pending_auth is None
    assert not transport.is_connected

    with pytest.raises(MQTTDisconnectedError):
        await protocol.reauthenticate()

    await _stop_task(read_task)


async def test_reauthenticate_timeout_covers_stalled_send() -> None:
    handler = FakeAuthHandler()
    protocol, transport = await _connected(handler)
    read_task = await _run_read_loop(protocol)
    _block_writes(transport)

    with pytest.raises(MQTTTimeoutError):
        await asyncio.wait_for(protocol.reauthenticate(timeout=0.05), timeout=1)

    assert protocol._state.pending_auth is None
    assert not transport.is_connected

    await _stop_task(read_task)


async def test_auth_without_pending_exchange_raises() -> None:
    handler = FakeAuthHandler()
    protocol, transport = await _connected(handler)
    transport.feed(encode(Auth(reason_code=0x00), version="5.0"))

    with pytest.raises(MQTTProtocolError):
        await asyncio.wait_for(protocol._read_loop(), timeout=1)


async def test_auth_in_v311_session_raises() -> None:
    protocol, transport = make_protocol(version="3.1.1")
    transport.feed(encode(Auth(reason_code=0x00), version="5.0"))

    with pytest.raises(MQTTProtocolError):
        await asyncio.wait_for(protocol._read_loop(), timeout=1)


async def test_auth_unexpected_reason_code_during_reauth_fails_pending() -> None:
    handler = FakeAuthHandler()
    protocol, transport = await _connected(handler)
    run_task = asyncio.create_task(protocol.run())
    await protocol.started_event.wait()

    reauth_task = asyncio.create_task(protocol.reauthenticate())
    await _answer_after(transport, sent=1, packet=_auth_packet(0x80))

    with pytest.raises(MQTTProtocolError):
        await run_task

    with pytest.raises(MQTTDisconnectedError):
        await reauth_task


async def test_continue_data_error_during_reauth_stops_connection() -> None:
    class RaisingHandler(FakeAuthHandler):
        async def continue_data(self, data: bytes | None) -> bytes | None:  # noqa: ARG002
            msg = "cannot build response"
            raise ValueError(msg)

    handler = RaisingHandler()
    protocol, transport = await _connected(handler)
    run_task = asyncio.create_task(protocol.run())
    await protocol.started_event.wait()

    reauth_task = asyncio.create_task(protocol.reauthenticate())
    await _answer_after(transport, sent=1, packet=_auth_packet(0x18, b"challenge"))

    with pytest.raises(ValueError, match="cannot build response"):
        await run_task

    with pytest.raises(MQTTDisconnectedError):
        await reauth_task


async def test_disconnect_during_reauthenticate_raises_auth_error() -> None:
    handler = FakeAuthHandler()
    protocol, transport = await _connected(handler)
    run_task = asyncio.create_task(protocol.run())
    await protocol.started_event.wait()

    reauth_task = asyncio.create_task(protocol.reauthenticate())
    await _answer_after(
        transport,
        sent=1,
        packet=Disconnect(reason_code=0x87, properties=DisconnectProperties(reason_string="bad creds")),
    )

    with pytest.raises(MQTTDisconnectedError):
        await run_task

    with pytest.raises(MQTTAuthError) as exc_info:
        await reauth_task

    assert exc_info.value.reason_code == 0x87
    assert exc_info.value.reason_string == "bad creds"


async def test_cancel_pending_fails_pending_reauthenticate() -> None:
    protocol, _transport = make_protocol(version="5.0")
    future: asyncio.Future[Auth] = asyncio.get_running_loop().create_future()
    protocol._state.pending_auth = future

    protocol._cancel_pending()

    with pytest.raises(MQTTDisconnectedError):
        await future


class _ClientFakeTransport:
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


def test_client_v311_rejects_auth_handler() -> None:
    handler = FakeAuthHandler()

    with pytest.raises(RuntimeError, match=r"auth_handler require MQTT 5\.0"):
        MQTTClient("localhost", version="3.1.1", auth_handler=handler)


async def test_client_connect_embeds_negotiated_method_and_initial_data() -> None:
    handler = FakeAuthHandler(method="TEST", responses=[b"init"])
    connack = _connack()
    transport = _ClientFakeTransport(feed=connack)

    async def factory(host: str, port: int, tls: object) -> Transport:  # noqa: ARG001
        return transport

    client = MQTTClient("localhost", version="5.0", auth_handler=handler, transport_factory=factory)

    await client._connect()

    sent = _decode(transport.sent[0])
    assert isinstance(sent, Connect)
    assert sent.properties is not None
    assert isinstance(sent.properties, ConnectProperties)
    assert sent.properties.authentication_method == "TEST"
    assert sent.properties.authentication_data == b"init"
    assert handler.initial_calls == 1


async def test_client_reconnect_calls_initial_data_again() -> None:
    handler = FakeAuthHandler(method="TEST", responses=[b"first", b"second"])
    connack = _connack()
    transports = [_ClientFakeTransport(feed=connack), _ClientFakeTransport(feed=connack)]
    made: list[_ClientFakeTransport] = []

    async def factory(host: str, port: int, tls: object) -> Transport:  # noqa: ARG001
        transport = transports[len(made)]
        made.append(transport)
        return transport

    client = MQTTClient(
        "localhost",
        version="5.0",
        auth_handler=handler,
        reconnect=ReconnectConfig(initial_delay=0.0, max_attempts=None),
        transport_factory=factory,
    )

    await client._connect_with_retry()
    await client._connect_with_retry()

    assert handler.initial_calls == 2


async def test_client_reauthenticate_without_connection_raises() -> None:
    handler = FakeAuthHandler()
    client = MQTTClient("localhost", version="5.0", auth_handler=handler)

    with pytest.raises(MQTTDisconnectedError):
        await client.reauthenticate()


async def test_client_reauthenticate_on_v311_raises() -> None:
    client = MQTTClient("localhost", version="3.1.1")

    with pytest.raises(RuntimeError, match=r"AUTH is not allowed in MQTT 3\.1\.1"):
        await client.reauthenticate()


async def test_client_auth_emits_deprecation_warning() -> None:
    client = MQTTClient("localhost", version="5.0")

    with pytest.warns(DeprecationWarning, match=r"auth\(\) is deprecated"), pytest.raises(MQTTDisconnectedError):
        await client.auth("TEST", b"data")


class _RaisingInitialDataHandler(FakeAuthHandler):
    async def initial_data(self) -> bytes | None:
        msg = "cannot build initial data"
        raise ValueError(msg)


class _HangingInitialDataHandler(FakeAuthHandler):
    async def initial_data(self) -> bytes | None:
        await asyncio.Event().wait()
        return None


async def test_client_connect_closes_transport_when_initial_data_raises() -> None:
    transport = _ClientFakeTransport()

    async def factory(host: str, port: int, tls: object) -> Transport:  # noqa: ARG001
        return transport

    client = MQTTClient(
        "localhost",
        version="5.0",
        auth_handler=_RaisingInitialDataHandler(),
        transport_factory=factory,
    )

    with pytest.raises(ValueError, match="cannot build initial data"):
        await client._connect()

    assert transport.closed
    assert transport.sent == []


async def test_client_connect_initial_data_is_bounded_by_connect_timeout() -> None:
    transport = _ClientFakeTransport()

    async def factory(host: str, port: int, tls: object) -> Transport:  # noqa: ARG001
        return transport

    client = MQTTClient(
        "localhost",
        version="5.0",
        auth_handler=_HangingInitialDataHandler(),
        mqtt_connect_timeout=0.05,
        transport_factory=factory,
    )

    with pytest.raises(MQTTTimeoutError):
        await asyncio.wait_for(client._connect(), timeout=1)

    assert transport.closed
    assert transport.sent == []


async def test_client_connect_closes_transport_when_finalize_data_raises() -> None:
    handler = FakeAuthHandler()
    handler.finalize_error = ValueError("bad server signature")
    transport = _ClientFakeTransport(feed=_connack(data=b"forged"))

    async def factory(host: str, port: int, tls: object) -> Transport:  # noqa: ARG001
        return transport

    client = MQTTClient("localhost", version="5.0", auth_handler=handler, transport_factory=factory)

    with pytest.raises(ValueError, match="bad server signature"):
        await client._connect()

    assert transport.closed
