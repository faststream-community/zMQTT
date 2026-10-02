import asyncio

import pytest

from zmqtt import (
    ConnAckProperties,
    MQTTClient,
    MQTTDisconnectedError,
    MQTTProtocolError,
    MQTTTopicAliasError,
    PublishProperties,
    QoS,
    ReconnectConfig,
    create_client,
)
from zmqtt._internal.packets.codec import decode, encode
from zmqtt._internal.packets.connect import ConnAck, Connect
from zmqtt._internal.packets.disconnect import Disconnect
from zmqtt._internal.packets.publish import PubAck, Publish
from zmqtt._internal.packets.subscribe import SubAck, Subscribe, UnsubAck, Unsubscribe

from .test_connect_properties import sent_packets
from .test_connection_info import transport_factory
from .test_protocol import FakeTransport


class AliasTransport(FakeTransport):
    async def write(self, data: bytes) -> None:
        await super().write(data)
        decoded = decode(data, version="5.0")
        assert decoded is not None
        packet = decoded[0]
        if isinstance(packet, Subscribe):
            self.feed(encode(SubAck(packet_id=packet.packet_id, return_codes=(0,)), version="5.0"))
        elif isinstance(packet, Unsubscribe):
            self.feed(encode(UnsubAck(packet_id=packet.packet_id), version="5.0"))


def make_transport(maximum: int | None = 5) -> AliasTransport:
    transport = AliasTransport()
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
    async with create_client("h", version="5.0", transport_factory=transport_factory(broker), topic_alias_maximum=7):
        packet = sent_packets(broker)[0]
        assert isinstance(packet, Connect)
        assert packet.properties is not None
        assert packet.properties.topic_alias_maximum == 7


def test_invalid_incoming_alias_limit() -> None:
    for maximum in (-1, 65536):
        with pytest.raises(ValueError, match="topic_alias_maximum"):
            create_client("h", version="5.0", topic_alias_maximum=maximum)


async def test_incoming_alias_resolves_and_rebinds_message_topic() -> None:
    broker = make_transport()
    async with (
        MQTTClient("h", version="5.0", transport_factory=transport_factory(broker), topic_alias_maximum=1) as client,
        client.subscribe("incoming/#") as sub,
    ):
        for index, (wire_topic, expected) in enumerate(
            [
                ("incoming/first", "incoming/first"),
                ("", "incoming/first"),
                ("incoming/second", "incoming/second"),
                ("", "incoming/second"),
            ]
        ):
            payload = str(index).encode()
            broker.feed(
                encode(
                    Publish(
                        topic=wire_topic,
                        payload=payload,
                        qos=QoS.AT_MOST_ONCE,
                        retain=False,
                        dup=False,
                        properties=PublishProperties(topic_alias=1),
                    ),
                    version="5.0",
                )
            )
            message = await asyncio.wait_for(sub.get_message(), 2)
            assert (message.topic, message.payload) == (expected, payload)


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
    async with (
        MQTTClient(
            "h", version="5.0", transport_factory=transport_factory(broker), topic_alias_maximum=maximum
        ) as client,
        client.subscribe("t") as sub,
    ):
        await client.publish("outgoing", b"register", properties=PublishProperties(topic_alias=1))
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
        with pytest.raises(MQTTProtocolError):
            await asyncio.wait_for(sub.get_message(), 2)
        assert sent_packets(broker)[-1] == Disconnect(reason_code=reason)
        with pytest.raises(MQTTDisconnectedError):
            await client.publish("t", b"p")


@pytest.mark.parametrize("cancel", [False, True])
async def test_interrupted_alias_write_disconnects_client(monkeypatch: pytest.MonkeyPatch, *, cancel: bool) -> None:
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
        task = asyncio.create_task(
            client.publish("t", b"p", qos=QoS.AT_LEAST_ONCE, properties=PublishProperties(topic_alias=1))
        )
        await asyncio.wait_for(started.wait(), 2)
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else OSError):
            await asyncio.wait_for(task, 2)
        with pytest.raises(MQTTDisconnectedError):
            await client.publish("t", b"p")


async def test_concurrent_rebindings_do_not_wait_for_ack(monkeypatch: pytest.MonkeyPatch) -> None:
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
        first = asyncio.create_task(client.publish("first", b"p", qos=QoS.AT_LEAST_ONCE, properties=alias))
        await asyncio.wait_for(started.wait(), 2)
        second = asyncio.create_task(client.publish("second", b"p", properties=alias))
        reuse = asyncio.create_task(client.publish("", b"reuse", properties=alias))
        try:
            await asyncio.sleep(0)
        finally:
            release.set()
        try:
            await asyncio.wait_for(asyncio.gather(second, reuse), 2)
            assert not first.done()
            packets = [packet for packet in sent_packets(broker) if isinstance(packet, Publish)]
            assert [packet.topic for packet in packets] == ["first", "second", ""]
            packet_id = packets[0].packet_id
            assert packet_id is not None
            broker.feed(encode(PubAck(packet_id=packet_id), version="5.0"))
            await asyncio.wait_for(first, 2)
        finally:
            for task in (first, second, reuse):
                task.cancel()
            await asyncio.gather(first, second, reuse, return_exceptions=True)


@pytest.mark.parametrize("maximum", [None, 0, 2])
async def test_outgoing_alias_respects_server_limit(maximum: int | None) -> None:
    broker = make_transport(maximum)
    async with MQTTClient("h", version="5.0", transport_factory=transport_factory(broker)) as client:
        for topic in ("t", ""):
            with pytest.raises(MQTTTopicAliasError):
                await client.publish(topic, b"p", properties=PublishProperties(topic_alias=(maximum or 0) + 1))
        await client.publish("t", b"valid")
        packets = [packet for packet in sent_packets(broker) if isinstance(packet, Publish)]
        assert [(packet.topic, packet.payload) for packet in packets] == [("t", b"valid")]
