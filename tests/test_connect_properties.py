"""MQTT 5.0 CONNECT properties: wire content, validation, and inbound limits.

Brokers never violate the limits a client advertises, so their enforcement is
exercised with a fake transport; tests/test_brokers covers compliant brokers.
"""

import asyncio
import ssl
from typing import Any

import pytest

from tests.test_connection_info import transport_factory
from tests.test_protocol import FakeTransport, _run_read_loop, _stop_task
from zmqtt import ConnAckProperties, ConnectProperties, MQTTClient, MQTTProtocolError, ReconnectConfig, create_client
from zmqtt._internal.packets.codec import AnyPacket, decode, encode
from zmqtt._internal.packets.connect import ConnAck, Connect
from zmqtt._internal.packets.disconnect import Disconnect
from zmqtt._internal.packets.publish import PubAck, PubComp, Publish, PubRec, PubRel
from zmqtt._internal.protocol import MQTTProtocol
from zmqtt._internal.state import SessionState
from zmqtt._internal.subscription_index import SubscriptionEntry
from zmqtt._internal.transport.base import Transport
from zmqtt._internal.types.message import Message
from zmqtt._internal.types.qos import QoS
from zmqtt.errors import MQTTDisconnectedError

CONNACK = encode(ConnAck(session_present=False, return_code=0), version="5.0")


def sent_packets(transport: FakeTransport) -> list[AnyPacket]:
    packets = []
    for data in transport.sent:
        decoded = decode(data, version="5.0")
        assert decoded is not None
        packets.append(decoded[0])
    return packets


def sent_connect(transport: FakeTransport) -> Connect:
    connect = sent_packets(transport)[0]
    assert isinstance(connect, Connect)
    return connect


def publish(packet_id: int | None, qos: QoS = QoS.AT_LEAST_ONCE, payload: bytes = b"x") -> bytes:
    packet = Publish(topic="t", payload=payload, qos=qos, retain=False, dup=False, packet_id=packet_id)
    return encode(packet, version="5.0")


async def connected_protocol(
    *,
    receive_maximum: int | None = None,
    maximum_packet_size: int | None = None,
    auto_ack: bool = False,
) -> tuple[MQTTProtocol, FakeTransport, asyncio.Queue[Message]]:
    transport = FakeTransport()
    protocol = MQTTProtocol(
        transport,
        SessionState(),
        version="5.0",
        receive_maximum=receive_maximum,
        maximum_packet_size=maximum_packet_size,
    )
    transport.feed(CONNACK)
    await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))
    transport.sent.clear()
    queue: asyncio.Queue[Message] = asyncio.Queue()
    protocol._state.subscriptions.add("t", SubscriptionEntry(queue=queue, auto_ack=auto_ack))
    return protocol, transport, queue


async def test_connect_properties_are_sent_on_every_attempt() -> None:
    first, retry = FakeTransport(), FakeTransport()
    first.feed(CONNACK)
    retry.feed(CONNACK)
    transports = iter([first, retry])

    async def factory(host: str, port: int, tls: ssl.SSLContext | bool | None) -> Transport:  # noqa: ARG001
        return next(transports)

    client = MQTTClient(
        "localhost",
        version="5.0",
        transport_factory=factory,
        reconnect=ReconnectConfig(initial_delay=0),
        session_expiry_interval=60,
        receive_maximum=10,
        maximum_packet_size=4096,
        user_properties=[("region", "eu"), ("tag", "a"), ("region", "us")],
        request_response_information=True,
        request_problem_information=False,
    )
    async with client:
        first._rx.append(MQTTDisconnectedError("lost"))

        async def reconnected() -> None:
            while True:
                await asyncio.sleep(0)
                info = client._connection_info
                if info is not None and info.connection_id == 2:
                    return

        await asyncio.wait_for(reconnected(), timeout=2)

    expected = ConnectProperties(
        session_expiry_interval=60,
        receive_maximum=10,
        maximum_packet_size=4096,
        request_response_information=True,
        request_problem_information=False,
        user_properties=(("region", "eu"), ("tag", "a"), ("region", "us")),
    )
    assert sent_connect(first).properties == expected
    assert sent_connect(retry).properties == expected


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({}, ConnectProperties(session_expiry_interval=0)),
        (
            {"receive_maximum": 65535, "request_response_information": False, "request_problem_information": True},
            ConnectProperties(
                session_expiry_interval=0,
                receive_maximum=65535,
                request_response_information=False,
                request_problem_information=True,
            ),
        ),
    ],
    ids=["omitted", "explicit-defaults"],
)
async def test_only_configured_properties_are_sent(options: dict[str, Any], expected: ConnectProperties) -> None:
    transport = FakeTransport()
    transport.feed(CONNACK)
    async with MQTTClient("localhost", version="5.0", transport_factory=transport_factory(transport), **options):
        pass
    assert sent_connect(transport).properties == expected


async def test_mqtt_v311_still_ignores_session_expiry_interval() -> None:
    transport = FakeTransport()
    transport.feed(encode(ConnAck(session_present=False, return_code=0), version="3.1.1"))
    client = MQTTClient("localhost", session_expiry_interval=3600, transport_factory=transport_factory(transport))
    async with client:
        pass
    assert sent_connect(transport).properties is None


def test_boundary_values_are_accepted() -> None:
    MQTTClient(
        "localhost",
        version="5.0",
        session_expiry_interval=0xFFFF_FFFF,
        receive_maximum=65535,
        maximum_packet_size=268_435_460,
        user_properties=[("", ""), ("key", "x" * 65535)],
    )
    MQTTClient("localhost", version="5.0", receive_maximum=1, maximum_packet_size=1)


@pytest.mark.parametrize(
    ("options", "match"),
    [
        ({"session_expiry_interval": -1}, r"session_expiry_interval must be in 0\.\.4294967295"),
        ({"session_expiry_interval": 2**32}, r"session_expiry_interval must be in 0\.\.4294967295"),
        ({"receive_maximum": 0}, r"receive_maximum must be in 1\.\.65535"),
        ({"receive_maximum": 65536}, r"receive_maximum must be in 1\.\.65535"),
        ({"maximum_packet_size": 0}, r"maximum_packet_size must be in 1\.\.268435460"),
        ({"maximum_packet_size": 268_435_461}, r"maximum_packet_size must be in 1\.\.268435460"),
        ({"user_properties": [("key", "a\x00b")]}, r"must not contain U\+0000"),
        ({"user_properties": [("\ud800", "value")]}, "must be valid UTF-8"),
        ({"user_properties": [("key", "é" * 32768)]}, "must not exceed 65535 UTF-8 bytes"),
    ],
)
def test_invalid_values_raise(options: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        MQTTClient("localhost", version="5.0", **options)


@pytest.mark.parametrize("user_properties", [{"key": "value"}, ["kv"], [("key",)], [("key", 1)]])
def test_user_properties_must_be_string_pairs(user_properties: object) -> None:
    with pytest.raises(TypeError, match=r"\(name, value\) string pairs"):
        MQTTClient("localhost", version="5.0", user_properties=user_properties)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "options",
    [
        {"receive_maximum": 10},
        {"maximum_packet_size": 1024},
        {"user_properties": [("key", "value")]},
        {"request_response_information": False},
        {"request_problem_information": True},
    ],
)
def test_mqtt_v311_rejects_connect_properties(options: dict[str, Any]) -> None:
    (name,) = options
    with pytest.raises(RuntimeError, match=rf"MQTT 5\.0 is required for {name}"):
        MQTTClient("localhost", version="3.1.1", **options)


def test_mqtt_v311_factory_rejects_connect_properties() -> None:
    with pytest.raises(RuntimeError, match=r"MQTT 5\.0 is required for receive_maximum"):
        create_client("localhost", receive_maximum=10)  # type: ignore[call-overload]


async def test_exceeding_receive_maximum_disconnects_with_0x93() -> None:
    protocol, transport, queue = await connected_protocol(receive_maximum=2)
    for packet_id in (1, 2, 3):
        transport.feed(publish(packet_id))

    with pytest.raises(MQTTProtocolError, match="Receive Maximum of 2"):
        await asyncio.wait_for(protocol._read_loop(), timeout=2)

    assert queue.qsize() == 2
    assert sent_packets(transport) == [Disconnect(reason_code=0x93)]
    assert not transport.is_connected


async def test_acknowledgements_free_receive_quota() -> None:
    protocol, transport, queue = await connected_protocol(receive_maximum=1)
    read = await _run_read_loop(protocol)
    try:
        for packet_id in (1, 2):
            transport.feed(publish(packet_id))
            transport.feed(publish(None, QoS.AT_MOST_ONCE))  # QoS 0 never uses quota
            message = await asyncio.wait_for(queue.get(), timeout=2)
            await asyncio.wait_for(queue.get(), timeout=2)
            await message.ack()
        assert not read.done()
    finally:
        await _stop_task(read)
    assert sent_packets(transport) == [PubAck(packet_id=1), PubAck(packet_id=2)]


async def test_qos2_holds_receive_quota_until_pubcomp() -> None:
    protocol, transport, _ = await connected_protocol(receive_maximum=1, auto_ack=True)
    transport.feed(publish(1, QoS.EXACTLY_ONCE))
    transport.feed(publish(1, QoS.EXACTLY_ONCE))  # retransmission keeps its unit
    transport.feed(encode(PubRel(packet_id=1), version="5.0"))
    transport.feed(publish(2, QoS.EXACTLY_ONCE))
    transport.feed(publish(3, QoS.EXACTLY_ONCE))  # 2 still awaits PUBREL

    with pytest.raises(MQTTProtocolError, match="Receive Maximum of 1"):
        await asyncio.wait_for(protocol._read_loop(), timeout=2)

    assert sent_packets(transport) == [
        PubRec(packet_id=1),
        PubRec(packet_id=1),
        PubComp(packet_id=1),
        PubRec(packet_id=2),
        Disconnect(reason_code=0x93),
    ]


async def test_oversized_packet_disconnects_with_0x95_before_its_body_arrives() -> None:
    protocol, transport, queue = await connected_protocol(maximum_packet_size=64)
    at_limit = publish(None, QoS.AT_MOST_ONCE, payload=b"x" * 58)
    oversized = publish(None, QoS.AT_MOST_ONCE, payload=b"x" * 200)
    assert len(at_limit) == 64
    transport.feed(at_limit)
    transport.feed(oversized[:3])  # the fixed header alone reveals the size

    with pytest.raises(MQTTProtocolError, match=f"packet of {len(oversized)} bytes, exceeding .* of 64 bytes"):
        await asyncio.wait_for(protocol._read_loop(), timeout=2)

    assert queue.qsize() == 1
    assert sent_packets(transport) == [Disconnect(reason_code=0x95)]
    assert not transport.is_connected


async def test_oversized_connack_is_rejected() -> None:
    transport = FakeTransport()
    protocol = MQTTProtocol(transport, SessionState(), version="5.0", maximum_packet_size=16)
    properties = ConnAckProperties(reason_string="x" * 32)
    transport.feed(encode(ConnAck(session_present=False, return_code=0, properties=properties), version="5.0"))

    with pytest.raises(MQTTProtocolError, match="Maximum Packet Size of 16 bytes"):
        await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))

    assert sent_packets(transport)[1:] == [Disconnect(reason_code=0x95)]
    assert not transport.is_connected


async def test_limit_violation_stops_client_without_reconnecting() -> None:
    made: list[FakeTransport] = []

    async def factory(host: str, port: int, tls: ssl.SSLContext | bool | None) -> Transport:  # noqa: ARG001
        transport = FakeTransport()
        transport.feed(CONNACK)
        made.append(transport)
        return transport

    client = MQTTClient(
        "localhost",
        version="5.0",
        maximum_packet_size=64,
        transport_factory=factory,
        reconnect=ReconnectConfig(initial_delay=0),
    )
    async with client:
        made[0].feed(publish(None, QoS.AT_MOST_ONCE, payload=b"x" * 200))
        assert client._run_task is not None
        with pytest.raises(MQTTProtocolError, match="Maximum Packet Size of 64 bytes"):
            await asyncio.wait_for(asyncio.shield(client._run_task), timeout=2)

    assert len(made) == 1
