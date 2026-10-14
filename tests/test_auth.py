"""Defensive AUTH cases; real SCRAM exchanges live in test_brokers/test_emqx_auth.py."""

import asyncio
from collections import deque

import pytest

from zmqtt import MQTTClient
from zmqtt._internal.packets.auth import Auth
from zmqtt._internal.packets.codec import AnyPacket, encode
from zmqtt._internal.packets.connect import ConnAck, Connect
from zmqtt._internal.packets.disconnect import Disconnect
from zmqtt._internal.packets.properties import (
    AuthProperties,
    ConnAckProperties,
    DisconnectProperties,
)
from zmqtt._internal.packets.reader import PacketBuffer
from zmqtt._internal.protocol import MQTTProtocol
from zmqtt.errors import (
    MQTTAuthError,
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


@pytest.mark.parametrize("rounds", [0, 2])
async def test_connect_unusual_exchange(rounds: int) -> None:
    handler = FakeAuthHandler(responses=[b"response"] * rounds)
    protocol, transport = make_protocol(version="5.0", auth_handler=handler)
    for _ in range(rounds):
        transport.feed(_auth(0x18, b"challenge"))
    transport.feed(_connack())
    ack = await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))
    assert ack.return_code == 0
    assert handler.continue_calls == [b"challenge"] * rounds
    assert handler.finalize_calls == [None]
    assert len(transport.sent) == rounds + 1


@pytest.mark.parametrize("handler_present", [True, False])
async def test_connect_unexpected_auth_raises(handler_present: bool) -> None:
    protocol, transport = make_protocol(version="5.0", auth_handler=FakeAuthHandler() if handler_present else None)
    transport.feed(encode(Auth(reason_code=0x00 if handler_present else 0x18), version="5.0"))
    with pytest.raises(MQTTProtocolError):
        await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))


@pytest.mark.parametrize("method", [None, "WRONG"])
@pytest.mark.parametrize("phase", ["connack", "challenge"])
async def test_connect_wrong_auth_method_raises(method: str | None, phase: str) -> None:
    handler = FakeAuthHandler(responses=[b"response"])
    protocol, transport = make_protocol(version="5.0", auth_handler=handler)
    transport.feed(_connack(method) if phase == "connack" else _auth(0x18, b"challenge", method))
    with pytest.raises(MQTTProtocolError, match="Authentication Method"):
        await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))
    assert protocol._state.auth_method is None
    assert handler.continue_calls == []
    assert handler.finalize_calls == []


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


def _block_writes(transport: FakeTransport) -> asyncio.Event:
    """Make ``transport.write`` hang until the returned event is set (a stalled send)."""
    release = asyncio.Event()

    async def write(data: bytes) -> None:
        await release.wait()
        transport.sent.append(data)

    transport.write = write  # type: ignore[method-assign]
    return release


@pytest.mark.parametrize("abandon", ["cancel", "timeout"])
async def test_reauthenticate_abandoned_during_send(abandon: str) -> None:
    protocol, transport = await _connected(FakeAuthHandler())
    read_task = await _run_read_loop(protocol)
    _block_writes(transport)
    task = asyncio.create_task(protocol.reauthenticate(timeout=0.05 if abandon == "timeout" else 1))
    try:
        await asyncio.sleep(0)
        if abandon == "cancel":
            task.cancel()
        with pytest.raises(asyncio.CancelledError if abandon == "cancel" else MQTTTimeoutError):
            await asyncio.wait_for(task, timeout=2)
        assert protocol._state.pending_auth is None
        assert not transport.is_connected
        with pytest.raises(MQTTDisconnectedError):
            await protocol.reauthenticate()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
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


@pytest.mark.parametrize("operation", ["constructor_v311", "reauth_v311", "disconnected"])
async def test_client_auth_guards(operation: str) -> None:
    if operation == "constructor_v311":
        with pytest.raises(RuntimeError, match=r"auth_handler require MQTT 5\.0"):
            MQTTClient("localhost", version="3.1.1", auth_handler=FakeAuthHandler())
    elif operation == "reauth_v311":
        with pytest.raises(RuntimeError, match=r"AUTH is not allowed in MQTT 3\.1\.1"):
            await MQTTClient("localhost", version="3.1.1").reauthenticate()
    else:
        with pytest.raises(MQTTDisconnectedError):
            await MQTTClient("localhost", version="5.0", auth_handler=FakeAuthHandler()).reauthenticate()


async def test_client_auth_emits_deprecation_warning() -> None:
    client = MQTTClient("localhost", version="5.0")

    with pytest.warns(DeprecationWarning, match=r"auth\(\) is deprecated"), pytest.raises(MQTTDisconnectedError):
        await client.auth("TEST", b"data")


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel", "cancel_after_abandon"])
async def test_run_finalizes_pending_operations(outcome: str, monkeypatch: pytest.MonkeyPatch) -> None:
    protocol, transport = await _connected(FakeAuthHandler())
    pending: asyncio.Future[Auth] = asyncio.get_running_loop().create_future()
    protocol._state.pending_auth = pending

    async def read_loop() -> None:
        if outcome == "failure":
            msg = "read failed"
            raise ValueError(msg)
        if outcome.startswith("cancel"):
            await asyncio.Event().wait()

    async def ping_loop() -> None:
        return

    monkeypatch.setattr(protocol, "_read_loop", read_loop)
    monkeypatch.setattr(protocol, "_ping_loop", ping_loop)
    task = asyncio.create_task(protocol.run())
    await protocol.started_event.wait()
    if outcome.startswith("cancel"):
        protocol._abandoned_reauth = outcome == "cancel_after_abandon"
        task.cancel()
    try:
        if outcome == "success":
            await asyncio.wait_for(task, timeout=1)
        else:
            with pytest.raises(ValueError if outcome == "failure" else asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=1)
        with pytest.raises(MQTTDisconnectedError) as error:
            await asyncio.wait_for(pending, timeout=1)
        assert isinstance(error.value.__cause__, ValueError) if outcome == "failure" else error.value.__cause__ is None
        assert protocol._dead
        assert not transport.is_connected
        with pytest.raises(MQTTDisconnectedError):
            await protocol.reauthenticate()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("abandon", ["timeout", "cancel"])
@pytest.mark.parametrize("late_error", [True, False])
async def test_late_finalize_does_not_complete_abandoned_future(abandon: str, late_error: bool) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class PausingHandler(FakeAuthHandler):
        async def finalize_data(self, data: bytes | None) -> None:
            if data == b"late":
                entered.set()
                await release.wait()
                if late_error:
                    msg = "late verification error"
                    raise ValueError(msg)
            await super().finalize_data(data)

    protocol, transport = await _connected(PausingHandler())
    transport.written.clear()
    reauth = asyncio.create_task(protocol.reauthenticate(timeout=0.1 if abandon == "timeout" else 1))
    await asyncio.wait_for(transport.written.wait(), timeout=1)
    callback = asyncio.create_task(protocol._handle_auth(_auth_packet(0x00, b"late")))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        if abandon == "cancel":
            reauth.cancel()
        with pytest.raises(MQTTTimeoutError if abandon == "timeout" else asyncio.CancelledError):
            await asyncio.wait_for(reauth, timeout=1)
        assert not transport.is_connected
        assert protocol._state.pending_auth is None
        release.set()
        if late_error:
            with pytest.raises(ValueError, match="late verification error"):
                await asyncio.wait_for(callback, timeout=1)
        else:
            await asyncio.wait_for(callback, timeout=1)
    finally:
        reauth.cancel()
        callback.cancel()
        await asyncio.gather(reauth, callback, return_exceptions=True)
