"""MQTT 5 enhanced authentication against the pinned EMQX SCRAM listener."""

import asyncio
import base64
import ssl
import uuid
from collections.abc import Callable

import pytest

from tests.test_brokers._scram import SCRAM_HOST, SCRAM_PASSWORD, SCRAM_PORT, SCRAM_USERNAME, ScramSHA256Handler
from zmqtt import (
    MQTTAuthError,
    MQTTClient,
    MQTTConnectError,
    MQTTDisconnectedError,
    MQTTTimeoutError,
    QoS,
    ReconnectConfig,
)
from zmqtt._internal.transport.base import Transport
from zmqtt._internal.transport.tcp import open_tcp

pytestmark = [pytest.mark.broker, pytest.mark.emqx_auth]
_TIMEOUT = 5.0


class ControlledScramHandler(ScramSHA256Handler):
    def __init__(self, username: str = SCRAM_USERNAME, password: str = SCRAM_PASSWORD) -> None:
        super().__init__(username, password)
        self.fail_stage: str | None = None
        self.pause_stage: str | None = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.callback_cancelled = asyncio.Event()
        self.corrupt_signature = False

    async def _control(self, stage: str) -> None:
        if stage == self.fail_stage:
            msg = f"handler failed at {stage}"
            raise ValueError(msg)
        if stage == self.pause_stage:
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.callback_cancelled.set()
                raise

    async def initial_data(self) -> bytes:
        await self._control("initial")
        return await super().initial_data()

    async def continue_data(self, data: bytes | None) -> bytes:
        await self._control("continue")
        return await super().continue_data(data)

    async def finalize_data(self, data: bytes | None) -> None:
        await self._control("finalize")
        if self.corrupt_signature:
            data = b"v=" + base64.b64encode(bytes(32))
        await super().finalize_data(data)


class ClientFactory:
    def __init__(self) -> None:
        self.transports: list[Transport] = []

    async def _open(self, host: str, port: int, tls: ssl.SSLContext | bool | None) -> Transport:  # noqa: ARG002
        transport = await open_tcp(host, port)
        self.transports.append(transport)
        return transport

    def create(
        self, handler: ScramSHA256Handler | None, *, reconnect: bool = False, connect_timeout: float = _TIMEOUT
    ) -> MQTTClient:
        return MQTTClient(
            SCRAM_HOST,
            SCRAM_PORT,
            version="5.0",
            auth_handler=handler,
            client_id=f"scram-{uuid.uuid4().hex}",
            mqtt_connect_timeout=connect_timeout,
            reconnect=ReconnectConfig(enabled=reconnect, initial_delay=0.05, max_attempts=3),
            transport_factory=self._open,
        )


@pytest.fixture
def factory() -> ClientFactory:
    return ClientFactory()


@pytest.fixture
def handler(scram_username: str) -> ControlledScramHandler:
    return ControlledScramHandler(username=scram_username)


async def _until(predicate: Callable[[], bool]) -> None:
    async def poll() -> None:
        while not predicate():  # noqa: ASYNC110 - observing external connection state
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout=_TIMEOUT)


async def _assert_dead(client: MQTTClient, factory: ClientFactory) -> None:
    await _until(lambda: all(not transport.is_connected for transport in factory.transports))
    for operation in (client.ping, client.reauthenticate):
        with pytest.raises(MQTTDisconnectedError):
            await asyncio.wait_for(operation(), timeout=_TIMEOUT)
    task = client._run_task
    assert task is not None
    results = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=_TIMEOUT)
    assert isinstance(results[0], Exception)


async def test_scram_connect_and_sequential_reauthentication(
    factory: ClientFactory, topic: str, handler: ControlledScramHandler
) -> None:
    async with factory.create(handler) as client, client.subscribe(topic, qos=QoS.AT_LEAST_ONCE) as subscription:
        assert handler.calls == ["initial", "continue", "finalize"]
        assert handler.verified == 1
        connection_id = client.connection_info.connection_id
        for index in range(3):
            if index:
                await client.reauthenticate(handler.begin_exchange(), timeout=_TIMEOUT)
            await client.ping(timeout=_TIMEOUT)
            payload = f"verified-{index}".encode()
            await asyncio.wait_for(client.publish(topic, payload, qos=QoS.AT_LEAST_ONCE), timeout=_TIMEOUT)
            message = await asyncio.wait_for(subscription.get_message(), timeout=_TIMEOUT)
            assert message.payload == payload
            assert client.connection_info.connection_id == connection_id
        assert handler.verified == 3
        assert handler.calls == ["initial", "continue", "finalize", "continue", "finalize", "continue", "finalize"]
        assert len(set(handler.nonces)) == 3
    assert all(not transport.is_connected for transport in factory.transports)


@pytest.mark.parametrize("failure", ["password", "username", "method", "no_handler"])
async def test_scram_connect_rejected(factory: ClientFactory, failure: str, handler: ControlledScramHandler) -> None:
    if failure == "password":
        handler.password = "incorrect"  # noqa: S105 - intentionally invalid test password
    elif failure == "username":
        handler.username = "unknown-scram-user"
    elif failure == "method":
        handler.method = "UNSUPPORTED"
    client = factory.create(None if failure == "no_handler" else handler)
    try:
        with pytest.raises(MQTTConnectError) as error:
            await asyncio.wait_for(client.connect(), timeout=_TIMEOUT + 1)
        assert error.value.return_code == 0x87
        assert handler.verified == 0
        assert "finalize" not in handler.calls
        assert all(not transport.is_connected for transport in factory.transports)
        with pytest.raises(MQTTDisconnectedError):
            await client.reauthenticate()
    finally:
        await client.disconnect()


async def test_scram_reauthentication_rejected(factory: ClientFactory, handler: ControlledScramHandler) -> None:
    async with factory.create(handler) as client:
        handler.password = "incorrect"  # noqa: S105 - intentionally invalid test password
        with pytest.raises(MQTTAuthError) as error:
            await asyncio.wait_for(client.reauthenticate(handler.begin_exchange(), timeout=_TIMEOUT), _TIMEOUT + 1)
        assert error.value.reason_code == 0x87
        assert handler.verified == 1
        await _assert_dead(client, factory)


@pytest.mark.parametrize("exchange", ["connect", "reauthenticate"])
async def test_scram_rejects_server_signature(
    factory: ClientFactory, exchange: str, handler: ControlledScramHandler
) -> None:
    client = factory.create(handler)
    try:
        if exchange == "reauthenticate":
            await client.connect()
        handler.corrupt_signature = True
        operation = (
            client.connect()
            if exchange == "connect"
            else client.reauthenticate(handler.begin_exchange(), timeout=_TIMEOUT)
        )
        with pytest.raises(ValueError, match="Invalid SCRAM server signature"):
            await asyncio.wait_for(operation, _TIMEOUT + 1)
        if exchange == "reauthenticate":
            await _assert_dead(client, factory)
        assert all(not transport.is_connected for transport in factory.transports)
        assert handler.verified == (1 if exchange == "reauthenticate" else 0)
    finally:
        await client.disconnect()


@pytest.mark.parametrize(
    ("exchange", "stage"), [("connect", "initial"), ("connect", "continue"), ("reauthenticate", "continue")]
)
async def test_scram_callback_failure(
    factory: ClientFactory, exchange: str, stage: str, handler: ControlledScramHandler
) -> None:
    client = factory.create(handler)
    try:
        if exchange == "reauthenticate":
            await client.connect()
        handler.fail_stage = stage
        expected = ValueError if exchange == "connect" else MQTTDisconnectedError
        operation = (
            client.connect()
            if exchange == "connect"
            else client.reauthenticate(handler.begin_exchange(), timeout=_TIMEOUT)
        )
        with pytest.raises(expected) as error:
            await asyncio.wait_for(operation, _TIMEOUT + 1)
        cause = error.value if exchange == "connect" else error.value.__cause__
        assert isinstance(cause, ValueError)
        assert str(cause) == f"handler failed at {stage}"
        if exchange == "reauthenticate":
            await _assert_dead(client, factory)
        assert all(not transport.is_connected for transport in factory.transports)
    finally:
        await client.disconnect()


async def test_scram_initial_callback_timeout(factory: ClientFactory, handler: ControlledScramHandler) -> None:
    handler.pause_stage = "initial"
    client = factory.create(handler, connect_timeout=0.1)
    try:
        with pytest.raises(MQTTTimeoutError):
            await asyncio.wait_for(client.connect(), _TIMEOUT)
        assert handler.callback_cancelled.is_set()
        assert handler.calls == []
        assert all(not transport.is_connected for transport in factory.transports)
    finally:
        await client.disconnect()


@pytest.mark.parametrize("stage", ["continue", "finalize"])
@pytest.mark.parametrize("abandon", ["timeout", "cancel"])
async def test_scram_abandoned_reauthentication(
    factory: ClientFactory, stage: str, abandon: str, handler: ControlledScramHandler
) -> None:
    async with factory.create(handler) as client:
        handler.pause_stage = stage
        task = asyncio.create_task(
            client.reauthenticate(handler.begin_exchange(), timeout=0.5 if abandon == "timeout" else 5)
        )
        try:
            await asyncio.wait_for(handler.entered.wait(), _TIMEOUT)
            if abandon == "cancel":
                task.cancel()
            with pytest.raises(MQTTTimeoutError if abandon == "timeout" else asyncio.CancelledError):
                await asyncio.wait_for(task, _TIMEOUT)
            await asyncio.wait_for(handler.callback_cancelled.wait(), _TIMEOUT)
            await _assert_dead(client, factory)
            assert handler.verified == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            handler.release.set()


async def test_scram_concurrent_reauthentication(factory: ClientFactory, handler: ControlledScramHandler) -> None:
    async with factory.create(handler) as client:
        handler.pause_stage = "continue"
        task = asyncio.create_task(client.reauthenticate(handler.begin_exchange(), timeout=_TIMEOUT))
        try:
            await asyncio.wait_for(handler.entered.wait(), _TIMEOUT)
            with pytest.raises(RuntimeError, match="already in progress"):
                await client.reauthenticate(b"must not be sent", timeout=_TIMEOUT)
            handler.release.set()
            await asyncio.wait_for(task, _TIMEOUT)
            assert handler.verified == 2
            await client.ping(timeout=_TIMEOUT)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_scram_automatic_reconnect(factory: ClientFactory, topic: str, handler: ControlledScramHandler) -> None:
    async with (
        factory.create(handler, reconnect=True) as client,
        client.subscribe(topic, qos=QoS.AT_LEAST_ONCE) as subscription,
    ):
        old_id = client.connection_info.connection_id
        await factory.transports[0].close()
        await _until(lambda: handler.verified == 2)
        await _until(
            lambda: (
                client._protocol is not None
                and client._protocol._state.subscriptions.contains(topic)
                and not client._protocol._state.pending_subs
            )
        )
        assert client.connection_info.connection_id > old_id
        assert handler.calls == ["initial", "continue", "finalize"] * 2
        assert len(set(handler.nonces)) == 2
        await asyncio.wait_for(client.publish(topic, b"after-reconnect", qos=QoS.AT_LEAST_ONCE), _TIMEOUT)
        message = await asyncio.wait_for(subscription.get_message(), _TIMEOUT)
        assert message.payload == b"after-reconnect"
    assert all(not transport.is_connected for transport in factory.transports)


async def test_reauthentication_requires_negotiated_method() -> None:
    async with MQTTClient(SCRAM_HOST, 1888, version="5.0", reconnect=ReconnectConfig(enabled=False)) as client:
        with pytest.raises(RuntimeError, match="requires a negotiated authentication method"):
            await client.reauthenticate(timeout=_TIMEOUT)
        await client.ping(timeout=_TIMEOUT)
