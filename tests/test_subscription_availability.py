"""Tests for MQTT 5 subscription capabilities advertised in CONNACK."""

import asyncio
from typing import Literal

import pytest

from zmqtt import MQTTClient, MQTTDisconnectedError, MQTTSubscriptionNotAvailableError
from zmqtt._internal.packets.codec import encode
from zmqtt._internal.packets.connect import ConnAck, Connect
from zmqtt._internal.packets.properties import ConnAckProperties
from zmqtt._internal.packets.subscribe import SubAck, SubscriptionRequest
from zmqtt._internal.protocol import MQTTProtocol
from zmqtt._internal.types.qos import QoS

from .test_protocol import FakeTransport, _run_read_loop, _stop_task, make_protocol

Version = Literal["3.1.1", "5.0"]


def _feed_connack(
    transport: FakeTransport,
    properties: ConnAckProperties | None,
    *,
    version: Version = "5.0",
) -> None:
    transport.feed(encode(ConnAck(session_present=False, return_code=0, properties=properties), version=version))


async def _connected_protocol(
    properties: ConnAckProperties | None,
    *,
    version: Version = "5.0",
) -> tuple[MQTTProtocol, FakeTransport]:
    protocol, transport = make_protocol(version=version)
    _feed_connack(transport, properties, version=version)
    await protocol.connect(Connect(client_id="c", clean_session=True, keepalive=60))
    transport.sent.clear()
    return protocol, transport


def _request(topic_filter: str) -> SubscriptionRequest:
    return SubscriptionRequest(topic_filter=topic_filter, qos=QoS.AT_MOST_ONCE)


async def _reject(
    protocol: MQTTProtocol,
    *filters: str,
    subscription_identifier: int | None = None,
) -> MQTTSubscriptionNotAvailableError:
    with pytest.raises(MQTTSubscriptionNotAvailableError) as exc_info:
        await asyncio.wait_for(
            protocol.subscribe(
                [_request(f) for f in filters],
                queue=asyncio.Queue(),
                subscription_identifier=subscription_identifier,
            ),
            timeout=0.1,
        )
    return exc_info.value


async def _subscribe_and_ack(
    protocol: MQTTProtocol,
    transport: FakeTransport,
    *filters: str,
    subscription_identifier: int | None = None,
    version: Version = "5.0",
) -> None:
    task = asyncio.create_task(
        protocol.subscribe(
            [_request(f) for f in filters],
            queue=asyncio.Queue(),
            subscription_identifier=subscription_identifier,
        )
    )
    await asyncio.sleep(0)
    transport.feed(encode(SubAck(packet_id=1, return_codes=(0x00,) * len(filters)), version=version))
    read_task = await _run_read_loop(protocol)
    try:
        await task
    finally:
        await _stop_task(read_task)


@pytest.mark.parametrize("topic_filter", ["sensors/#", "a/+/b"])
async def test_wildcard_subscription_rejected_before_send(topic_filter: str) -> None:
    protocol, transport = await _connected_protocol(ConnAckProperties(wildcard_subscription_available=False))

    error = await _reject(protocol, topic_filter)

    assert error.capability == "wildcard_subscription"
    assert error.filters == (topic_filter,)
    assert "Wildcard Subscription Available is 0" in str(error)
    assert transport.sent == []  # nothing left the wire
    assert transport.is_connected  # the connection stays open


async def test_shared_subscription_rejected_before_send() -> None:
    protocol, transport = await _connected_protocol(ConnAckProperties(shared_subscription_available=False))

    error = await _reject(protocol, "$share/workers/jobs")

    assert error.capability == "shared_subscription"
    assert error.filters == ("$share/workers/jobs",)
    assert "Shared Subscription Available is 0" in str(error)
    assert transport.sent == []


async def test_shared_reported_first_when_both_capabilities_unavailable() -> None:
    protocol, _ = await _connected_protocol(
        ConnAckProperties(
            shared_subscription_available=False,
            wildcard_subscription_available=False,
        )
    )

    error = await _reject(protocol, "$share/g/a/#")

    assert error.capability == "shared_subscription"


async def test_subscription_identifier_rejected_before_send() -> None:
    protocol, transport = await _connected_protocol(ConnAckProperties(subscription_identifier_available=False))

    error = await _reject(protocol, "plain/topic", subscription_identifier=7)

    assert error.capability == "subscription_identifier"
    assert error.subscription_identifier == 7
    assert error.filters == ()
    assert "Subscription Identifier Available is 0" in str(error)
    assert transport.sent == []


async def test_wildcard_reported_before_identifier() -> None:
    protocol, _ = await _connected_protocol(
        ConnAckProperties(
            wildcard_subscription_available=False,
            subscription_identifier_available=False,
        )
    )

    error = await _reject(protocol, "sensors/#", subscription_identifier=7)

    assert error.capability == "wildcard_subscription"


async def test_error_lists_only_offending_filters() -> None:
    protocol, _ = await _connected_protocol(ConnAckProperties(wildcard_subscription_available=False))

    error = await _reject(protocol, "plain/topic", "sensors/+")

    assert error.filters == ("sensors/+",)


@pytest.mark.parametrize(
    "properties",
    [
        None,
        ConnAckProperties(session_expiry_interval=60),
        ConnAckProperties(
            wildcard_subscription_available=True,
            shared_subscription_available=True,
            subscription_identifier_available=True,
        ),
    ],
    ids=["no-properties", "properties-absent", "all-available"],
)
async def test_subscribe_sent_when_capabilities_available(properties: ConnAckProperties | None) -> None:
    # Absent properties mean "supported" (MQTT 5 §3.2.2.3.11-13).
    protocol, transport = await _connected_protocol(properties)

    await _subscribe_and_ack(protocol, transport, "$share/g/a/#", subscription_identifier=7)

    assert len(transport.sent) == 1
    assert protocol._state.subscriptions.by_identifier(7) != []


async def test_plain_subscribe_allowed_when_identifier_unavailable() -> None:
    protocol, transport = await _connected_protocol(ConnAckProperties(subscription_identifier_available=False))

    await _subscribe_and_ack(protocol, transport, "plain/topic")

    assert len(transport.sent) == 1
    assert protocol._state.subscriptions.get("plain/topic") is not None


@pytest.mark.parametrize("topic_filter", ["$queue/jobs", "$exclusive/jobs"])
async def test_broker_specific_prefixes_are_not_shared(topic_filter: str) -> None:
    # Only $share/<group>/... is a shared subscription per the MQTT spec.
    protocol, transport = await _connected_protocol(ConnAckProperties(shared_subscription_available=False))

    await _subscribe_and_ack(protocol, transport, topic_filter)

    assert len(transport.sent) == 1


async def test_v311_subscription_sent_regardless_of_capabilities() -> None:
    protocol, transport = await _connected_protocol(None, version="3.1.1")

    await _subscribe_and_ack(protocol, transport, "a/#", version="3.1.1")

    assert len(transport.sent) == 1


async def test_failed_start_removes_subscription_from_client() -> None:
    protocol, transport = await _connected_protocol(ConnAckProperties(wildcard_subscription_available=False))
    client = MQTTClient("localhost", version="5.0")
    client._protocol = protocol

    subscription = client.subscribe("sensors/#")
    with pytest.raises(MQTTSubscriptionNotAvailableError):
        await subscription.start()

    assert subscription not in client._subscriptions
    assert transport.sent == []


async def test_restore_detaches_unsupported_and_keeps_others() -> None:
    protocol, transport = await _connected_protocol(ConnAckProperties(wildcard_subscription_available=False))
    client = MQTTClient("localhost", version="5.0")
    client._protocol = protocol

    unsupported = client.subscribe("sensors/#")
    unsupported._registered_filters = ["sensors/#"]
    restored = client.subscribe("plain/topic")
    restored._registered_filters = ["plain/topic"]
    client._subscriptions.extend([unsupported, restored])

    restore_task = asyncio.create_task(client._restore_subscriptions(protocol, [unsupported, restored]))
    await asyncio.sleep(0)
    transport.feed(encode(SubAck(packet_id=1, return_codes=(0x00,)), version="5.0"))
    read_task = await _run_read_loop(protocol)
    try:
        await restore_task
    finally:
        await _stop_task(read_task)

    assert unsupported._detached
    assert unsupported._registered_filters == []
    with pytest.raises(MQTTDisconnectedError):
        await unsupported.get_message()
    assert restored in client._subscriptions
    assert restored._registered_filters == ["plain/topic"]
    assert len(transport.sent) == 1
