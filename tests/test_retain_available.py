"""Tests for MQTT 5 Retain Available (server capability from CONNACK)."""

import pytest

from zmqtt import MQTTRetainNotAvailableError
from zmqtt._internal.packets.codec import encode
from zmqtt._internal.packets.connect import ConnAck, Connect
from zmqtt._internal.packets.properties import ConnAckProperties
from zmqtt._internal.packets.publish import Publish
from zmqtt._internal.protocol import MQTTProtocol
from zmqtt._internal.types.qos import QoS

from .test_protocol import FakeTransport, make_protocol


def _feed_connack(transport: FakeTransport, properties: ConnAckProperties | None) -> None:
    transport.feed(encode(ConnAck(session_present=False, return_code=0, properties=properties), version="5.0"))


async def _connected_protocol(properties: ConnAckProperties | None) -> tuple[MQTTProtocol, FakeTransport]:
    protocol, transport = make_protocol(version="5.0")
    _feed_connack(transport, properties)
    await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))
    transport.sent.clear()
    return protocol, transport


def _publish(*, retain: bool) -> Publish:
    return Publish(topic="t/x", payload=b"p", qos=QoS.AT_MOST_ONCE, retain=retain, dup=False, packet_id=0)


async def test_retained_publish_rejected_before_send_when_unavailable() -> None:
    protocol, transport = await _connected_protocol(ConnAckProperties(retain_available=False))

    with pytest.raises(MQTTRetainNotAvailableError):
        await protocol.publish(_publish(retain=True))

    assert transport.sent == []  # nothing left the wire
    assert transport.is_connected  # the connection stays open


async def test_non_retained_publish_allowed_when_retain_unavailable() -> None:
    protocol, transport = await _connected_protocol(ConnAckProperties(retain_available=False))

    await protocol.publish(_publish(retain=False))

    assert len(transport.sent) == 1


@pytest.mark.parametrize(
    "properties",
    [
        None,
        ConnAckProperties(session_expiry_interval=60),
        ConnAckProperties(retain_available=True),
    ],
    ids=["no-properties", "property-absent", "retain-available"],
)
async def test_retained_publish_sent_when_retain_available(properties: ConnAckProperties | None) -> None:
    protocol, transport = await _connected_protocol(properties)

    await protocol.publish(_publish(retain=True))

    assert len(transport.sent) == 1


async def test_v311_retained_publish_is_sent() -> None:
    protocol, transport = make_protocol(version="3.1.1")
    transport.feed(encode(ConnAck(session_present=False, return_code=0), version="3.1.1"))
    await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))
    transport.sent.clear()

    await protocol.publish(_publish(retain=True))

    assert len(transport.sent) == 1


async def test_reconnect_refreshes_retain_available() -> None:
    # _connect_with_retry builds a new MQTTProtocol per attempt; the new
    # CONNACK's capability is the one that applies.
    protocol1, transport1 = await _connected_protocol(ConnAckProperties(retain_available=False))
    with pytest.raises(MQTTRetainNotAvailableError):
        await protocol1.publish(_publish(retain=True))
    assert transport1.sent == []

    protocol2, transport2 = await _connected_protocol(ConnAckProperties(retain_available=True))
    await protocol2.publish(_publish(retain=True))
    assert len(transport2.sent) == 1
