import asyncio

import pytest

from zmqtt import (
    ConnAckProperties,
    ConnectProperties,
    MQTTClient,
    MQTTDisconnectedError,
    MQTTProtocolError,
    MQTTTopicAliasError,
    PublishProperties,
    QoS,
    ReconnectConfig,
    create_client,
)
from zmqtt._internal.packets.codec import encode
from zmqtt._internal.packets.connect import ConnAck, Connect
from zmqtt._internal.packets.disconnect import Disconnect
from zmqtt._internal.packets.publish import Publish
from zmqtt._internal.packets.subscribe import SubAck, Subscribe

from .test_connect_properties import sent_packets
from .test_connection_info import transport_factory
from .test_protocol import FakeTransport


def make_transport(maximum: int | None = 5) -> FakeTransport:
    transport = FakeTransport()
    transport.feed(
        encode(
            ConnAck(
                session_present=False,
                return_code=0,
                properties=ConnAckProperties(topic_alias_maximum=maximum),
            ),
            version="5.0",
        )
    )
    return transport


async def test_connect_advertises_incoming_alias_limit() -> None:
    broker = make_transport()
    async with create_client(
        "h",
        version="5.0",
        transport_factory=transport_factory(broker),
        topic_alias_maximum=7,
        receive_maximum=10,
        maximum_packet_size=4096,
        user_properties=(("key", "value"),),
    ):
        packet = sent_packets(broker)[0]
        assert isinstance(packet, Connect)
        assert packet.properties == ConnectProperties(
            session_expiry_interval=0,
            topic_alias_maximum=7,
            receive_maximum=10,
            maximum_packet_size=4096,
            user_properties=(("key", "value"),),
        )


@pytest.mark.parametrize(
    ("maximum", "topic", "alias", "reason"),
    [
        (0, "t", 1, 0x94),
        (3, "t", 0, 0x94),
        (3, "t", 4, 0x94),
        (3, "", 1, 0x82),
        (3, "", None, 0x82),
    ],
)
async def test_invalid_incoming_alias_fails_subscription_and_disconnects(
    maximum: int,
    topic: str,
    alias: int | None,
    reason: int,
) -> None:
    broker = make_transport()
    async with MQTTClient(
        "h", version="5.0", transport_factory=transport_factory(broker), topic_alias_maximum=maximum
    ) as client:
        sub = client.subscribe("t")
        start = asyncio.create_task(sub.start())
        await asyncio.sleep(0)
        subscribe = sent_packets(broker)[-1]
        assert isinstance(subscribe, Subscribe)
        broker.feed(encode(SubAck(packet_id=subscribe.packet_id, return_codes=(0,)), version="5.0"))
        await asyncio.wait_for(start, 2)
        broker.feed(
            encode(
                Publish(
                    topic=topic,
                    payload=b"p",
                    qos=QoS.AT_MOST_ONCE,
                    retain=False,
                    dup=False,
                    properties=PublishProperties(topic_alias=alias),
                ),
                version="5.0",
            )
        )
        try:
            with pytest.raises(MQTTProtocolError):
                await asyncio.wait_for(sub.get_message(), 2)
            assert sent_packets(broker)[-1] == Disconnect(reason_code=reason)
            assert not broker.is_connected
        finally:
            await sub.stop()


@pytest.mark.parametrize(
    ("qos", "cancel"), [(QoS.AT_MOST_ONCE, True), (QoS.AT_LEAST_ONCE, False), (QoS.EXACTLY_ONCE, True)]
)
async def test_interrupted_alias_write_disconnects_client(
    monkeypatch: pytest.MonkeyPatch, qos: QoS, *, cancel: bool
) -> None:
    broker = make_transport()
    started = asyncio.Event()
    original_write = broker.write
    async with MQTTClient(
        "h", version="5.0", transport_factory=transport_factory(broker), reconnect=ReconnectConfig(enabled=False)
    ) as client:

        async def interrupted_write(data: bytes) -> None:
            if started.is_set():
                await original_write(data)
                return
            broker.sent.append(data)
            started.set()
            if cancel:
                await asyncio.Event().wait()
            msg = "write failed"
            raise OSError(msg)

        monkeypatch.setattr(broker, "write", interrupted_write)
        task = asyncio.create_task(client.publish("t", b"p", qos=qos, properties=PublishProperties(topic_alias=1)))
        await asyncio.wait_for(started.wait(), 2)
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else OSError):
            await asyncio.wait_for(task, 2)
        assert not broker.is_connected
        with pytest.raises(MQTTDisconnectedError):
            await client.publish("t", b"p")


async def test_concurrent_rebindings_are_sent_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    broker = make_transport()
    started, release = asyncio.Event(), asyncio.Event()
    original_write = broker.write
    async with MQTTClient("h", version="5.0", transport_factory=transport_factory(broker)) as client:

        async def delayed_write(data: bytes) -> None:
            if not started.is_set():
                started.set()
                await release.wait()
            await original_write(data)

        monkeypatch.setattr(broker, "write", delayed_write)
        alias = PublishProperties(topic_alias=1)
        first = asyncio.create_task(client.publish("first", b"p", properties=alias))
        await asyncio.wait_for(started.wait(), 2)
        second = asyncio.create_task(client.publish("second", b"p", properties=alias))
        try:
            await asyncio.sleep(0)
            assert not any(isinstance(packet, Publish) for packet in sent_packets(broker))
        finally:
            release.set()
            await asyncio.wait_for(asyncio.gather(first, second), 2)
        await client.publish("", b"p", properties=alias)
        assert [packet.topic for packet in sent_packets(broker) if isinstance(packet, Publish)] == [
            "first",
            "second",
            "",
        ]


@pytest.mark.parametrize("maximum", [None, 0, 2])
async def test_outgoing_alias_respects_server_limit(maximum: int | None) -> None:
    transport = make_transport(maximum)
    async with MQTTClient("h", version="5.0", transport_factory=transport_factory(transport)) as client:
        transport.sent.clear()
        with pytest.raises(MQTTTopicAliasError):
            await client.publish("t", b"p", properties=PublishProperties(topic_alias=(maximum or 0) + 1))
        assert not transport.sent
