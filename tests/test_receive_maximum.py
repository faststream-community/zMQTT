"""Tests for MQTT 5 outgoing flow control using the server's Receive Maximum."""

import asyncio
import struct
from typing import Literal

import pytest

from zmqtt._internal.packets.codec import AnyPacket, encode
from zmqtt._internal.packets.connect import ConnAck, Connect
from zmqtt._internal.packets.ping import PingReq, PingResp
from zmqtt._internal.packets.properties import ConnAckProperties
from zmqtt._internal.packets.publish import PubAck, PubComp, Publish, PubRec, PubRel
from zmqtt._internal.packets.reader import PacketBuffer
from zmqtt._internal.protocol import MQTTProtocol
from zmqtt._internal.types.qos import QoS
from zmqtt.errors import MQTTDisconnectedError, MQTTPublishError

from .test_protocol import FakeTransport, _run_read_loop, make_protocol


def _publish(payload: bytes, qos: QoS, *, topic: str = "t/x") -> Publish:
    return Publish(topic=topic, payload=payload, qos=qos, retain=False, dup=False)


def _sent_packets(transport: FakeTransport) -> list[AnyPacket]:
    buffer = PacketBuffer(version="5.0")
    for data in transport.sent:
        buffer.feed(data)
    return list(buffer)


async def _connected_protocol() -> tuple[MQTTProtocol, FakeTransport]:
    protocol, transport = make_protocol(version="5.0")
    transport.feed(
        encode(
            ConnAck(session_present=False, return_code=0, properties=ConnAckProperties(receive_maximum=1)),
            version="5.0",
        ),
    )
    await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))
    transport.sent.clear()
    transport.written.clear()
    return protocol, transport


async def _publish_after_started(
    protocol: MQTTProtocol,
    packet: Publish,
    started: asyncio.Event,
) -> PubAck | PubComp | None:
    started.set()
    return await protocol.publish(packet)


def _complete_publish(transport: FakeTransport, packet: Publish) -> PubAck | PubComp:
    assert packet.packet_id is not None
    if packet.qos is QoS.EXACTLY_ONCE:
        transport.feed(encode(PubRec(packet_id=packet.packet_id), version="5.0"))
        transport.feed(encode(PubComp(packet_id=packet.packet_id), version="5.0"))
        return PubComp(packet_id=packet.packet_id)
    transport.feed(encode(PubAck(packet_id=packet.packet_id), version="5.0"))
    return PubAck(packet_id=packet.packet_id)


@pytest.mark.parametrize(
    ("release_path", "first_qos", "second_qos"),
    [
        pytest.param("puback", QoS.AT_LEAST_ONCE, QoS.EXACTLY_ONCE, id="puback-qos1-to-qos2"),
        pytest.param("pubcomp", QoS.EXACTLY_ONCE, QoS.AT_LEAST_ONCE, id="pubcomp-qos2-to-qos1"),
        pytest.param("negative-pubrec", QoS.EXACTLY_ONCE, QoS.AT_LEAST_ONCE, id="negative-pubrec"),
    ],
)
async def test_terminal_ack_releases_shared_send_quota(
    release_path: Literal["puback", "pubcomp", "negative-pubrec"],
    first_qos: QoS,
    second_qos: QoS,
) -> None:
    protocol, transport = await _connected_protocol()
    read_task = await _run_read_loop(protocol)

    first_task = asyncio.create_task(protocol.publish(_publish(b"first", first_qos)))
    await asyncio.wait_for(transport.written.wait(), timeout=1)
    transport.written.clear()
    first = _sent_packets(transport)[0]
    assert isinstance(first, Publish)
    assert first.packet_id is not None

    second_started = asyncio.Event()
    second_task = asyncio.create_task(
        _publish_after_started(protocol, _publish(b"second", second_qos), second_started),
    )
    await asyncio.wait_for(second_started.wait(), timeout=1)
    assert _sent_packets(transport) == [first]
    assert not second_task.done()

    if release_path == "puback":
        first_ack: PubAck | PubComp = PubAck(packet_id=first.packet_id)
        transport.feed(encode(first_ack, version="5.0"))
        assert await asyncio.wait_for(first_task, timeout=1) == first_ack
    elif release_path == "pubcomp":
        transport.feed(encode(PubRec(packet_id=first.packet_id), version="5.0"))
        await asyncio.wait_for(transport.written.wait(), timeout=1)
        assert _sent_packets(transport) == [first, PubRel(packet_id=first.packet_id)]
        assert not second_task.done()
        transport.written.clear()
        first_ack = PubComp(packet_id=first.packet_id)
        transport.feed(encode(first_ack, version="5.0"))
        assert await asyncio.wait_for(first_task, timeout=1) == first_ack
    else:
        transport.feed(encode(PubRec(packet_id=first.packet_id, reason_code=0x80), version="5.0"))
        with pytest.raises(MQTTPublishError):
            await asyncio.wait_for(first_task, timeout=1)

    await asyncio.wait_for(transport.written.wait(), timeout=1)
    sent = _sent_packets(transport)
    if release_path == "negative-pubrec":
        assert not any(isinstance(packet, PubRel) for packet in sent)
    publications = [packet for packet in sent if isinstance(packet, Publish)]
    assert len(publications) == 2
    second = publications[1]
    assert second.payload == b"second"
    assert second.qos is second_qos

    second_ack = _complete_publish(transport, second)
    assert await asyncio.wait_for(second_task, timeout=1) == second_ack

    read_task.cancel()
    await asyncio.gather(read_task, return_exceptions=True)


async def test_cancelling_sent_publish_does_not_release_send_quota() -> None:
    protocol, transport = await _connected_protocol()
    read_task = await _run_read_loop(protocol)

    first_task = asyncio.create_task(protocol.publish(_publish(b"first", QoS.AT_LEAST_ONCE)))
    await asyncio.wait_for(transport.written.wait(), timeout=1)
    transport.written.clear()
    first = _sent_packets(transport)[0]
    assert isinstance(first, Publish)
    assert first.packet_id is not None

    first_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_task

    second_started = asyncio.Event()
    second_task = asyncio.create_task(
        _publish_after_started(protocol, _publish(b"second", QoS.AT_LEAST_ONCE), second_started),
    )
    await asyncio.wait_for(second_started.wait(), timeout=1)
    assert _sent_packets(transport) == [first]
    assert not second_task.done()

    transport.feed(encode(PubAck(packet_id=first.packet_id), version="5.0"))
    await asyncio.wait_for(transport.written.wait(), timeout=1)
    second = _sent_packets(transport)[1]
    assert isinstance(second, Publish)
    assert second.packet_id is not None

    second_ack = PubAck(packet_id=second.packet_id)
    transport.feed(encode(second_ack, version="5.0"))
    assert await asyncio.wait_for(second_task, timeout=1) == second_ack

    read_task.cancel()
    await asyncio.gather(read_task, return_exceptions=True)


async def test_disconnect_fails_publish_waiting_for_send_quota() -> None:
    protocol, transport = await _connected_protocol()
    run_task = asyncio.create_task(protocol.run())
    await asyncio.wait_for(protocol.started_event.wait(), timeout=1)

    first_task = asyncio.create_task(protocol.publish(_publish(b"first", QoS.AT_LEAST_ONCE)))
    await asyncio.wait_for(transport.written.wait(), timeout=1)
    transport.written.clear()

    second_started = asyncio.Event()
    second_task = asyncio.create_task(
        _publish_after_started(protocol, _publish(b"second", QoS.EXACTLY_ONCE), second_started),
    )
    await asyncio.wait_for(second_started.wait(), timeout=1)
    assert len(_sent_packets(transport)) == 1
    assert not second_task.done()

    transport._rx.append(MQTTDisconnectedError("connection lost"))
    with pytest.raises(MQTTDisconnectedError):
        await asyncio.wait_for(run_task, timeout=1)
    with pytest.raises(MQTTDisconnectedError):
        await asyncio.wait_for(first_task, timeout=1)
    with pytest.raises(MQTTDisconnectedError):
        await asyncio.wait_for(second_task, timeout=1)


@pytest.mark.parametrize(
    ("failure", "qos"),
    [
        pytest.param("encoding", QoS.AT_LEAST_ONCE, id="encoding-qos1"),
        pytest.param("packet-id", QoS.EXACTLY_ONCE, id="packet-id-qos2"),
    ],
)
async def test_preparation_failure_releases_send_quota(
    failure: Literal["encoding", "packet-id"],
    qos: QoS,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol, transport = await _connected_protocol()

    if failure == "encoding":
        with pytest.raises(struct.error):
            await protocol.publish(_publish(b"invalid", qos, topic="x" * 65_536))
    else:
        with monkeypatch.context() as patch:

            def fail_to_acquire_packet_id() -> int:
                msg = "All packet IDs are in use"
                raise RuntimeError(msg)

            patch.setattr(protocol._state.packet_ids, "acquire", fail_to_acquire_packet_id)
            with pytest.raises(RuntimeError, match="All packet IDs are in use"):
                await protocol.publish(_publish(b"invalid", qos))

    assert transport.sent == []

    read_task = await _run_read_loop(protocol)
    valid_task = asyncio.create_task(protocol.publish(_publish(b"valid", qos)))
    await asyncio.wait_for(transport.written.wait(), timeout=1)
    valid = _sent_packets(transport)[0]
    assert isinstance(valid, Publish)
    assert valid.packet_id == 1

    expected_ack = _complete_publish(transport, valid)
    assert await asyncio.wait_for(valid_task, timeout=1) == expected_ack

    read_task.cancel()
    await asyncio.gather(read_task, return_exceptions=True)


async def test_qos0_and_ping_continue_while_send_quota_is_exhausted() -> None:
    protocol, transport = await _connected_protocol()
    read_task = await _run_read_loop(protocol)

    first_task = asyncio.create_task(protocol.publish(_publish(b"first", QoS.AT_LEAST_ONCE)))
    await asyncio.wait_for(transport.written.wait(), timeout=1)

    assert await protocol.publish(_publish(b"qos0", QoS.AT_MOST_ONCE)) is None

    transport.written.clear()
    ping_task = asyncio.create_task(protocol.ping())
    await asyncio.wait_for(transport.written.wait(), timeout=1)
    sent = _sent_packets(transport)
    assert isinstance(sent[0], Publish)
    assert sent[0].qos is QoS.AT_LEAST_ONCE
    assert isinstance(sent[1], Publish)
    assert sent[1].qos is QoS.AT_MOST_ONCE
    assert sent[2] == PingReq()

    transport.feed(encode(PingResp(), version="5.0"))
    await asyncio.wait_for(ping_task, timeout=1)

    first_task.cancel()
    read_task.cancel()
    await asyncio.gather(first_task, read_task, return_exceptions=True)
