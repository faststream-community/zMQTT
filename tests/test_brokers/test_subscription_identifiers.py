"""E2E: MQTT 5 subscription-identifier routing, against Mosquitto.

Scoped to one broker on purpose. The interesting case — a ``$share`` subscription
and its plain twin on the same topics — is not portable across every broker in the
matrix (Artemis, for one, disconnects on it), so this runs against Mosquitto (the
port-1884 service, same as CI), where the behaviour is well defined.
"""

import asyncio
import uuid

import pytest

from zmqtt import MQTTClient, QoS

pytestmark = pytest.mark.broker

HOST = "127.0.0.1"
PORT = 1884  # mosquitto, MQTT 5.0


async def test_subscription_identifier_routes_overlapping_subscriptions() -> None:
    """The broker's echoed identifier routes each PUBLISH to the subscription that matched.

    A ``$share`` subscription (id 1) and a plain subscription on the same topics (id 2)
    are indistinguishable by client-side filter matching alone. With identifiers each
    receives its own copy, tagged with its own id; without them one starves while the
    other receives both.
    """
    bare = f"demo/{uuid.uuid4().hex[:8]}/state"
    group = f"zmqtt-si-{uuid.uuid4().hex[:8]}"

    async with (
        MQTTClient(HOST, PORT, version="5.0") as client,
        client.subscribe(f"$share/{group}/{bare}", qos=QoS.AT_LEAST_ONCE, subscription_identifier=1) as shared,
        client.subscribe(bare, qos=QoS.AT_LEAST_ONCE, subscription_identifier=2) as plain,
        MQTTClient(HOST, PORT, version="5.0") as publisher,
    ):
        for i in range(3):
            await publisher.publish(bare, f"m{i}".encode(), qos=QoS.AT_LEAST_ONCE)

        shared_msgs = [await asyncio.wait_for(shared.get_message(), timeout=5.0) for _ in range(3)]
        plain_msgs = [await asyncio.wait_for(plain.get_message(), timeout=5.0) for _ in range(3)]

    assert {m.properties.subscription_identifier for m in shared_msgs if m.properties} == {1}
    assert {m.properties.subscription_identifier for m in plain_msgs if m.properties} == {2}
