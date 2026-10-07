import asyncio
from collections import Counter

import pytest

from tests.test_brokers._base import BrokerTestBase
from zmqtt import MQTTClient, QoS, Subscription


class BaseTestNanoMQ(BrokerTestBase):
    supports_persistent_sessions = False

    async def handle_sub_duplicates(
        self,
        *,
        sub: Subscription,
        n_duplicates: int,
    ) -> None:
        for _ in range(n_duplicates):
            await sub.get_message()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(sub.get_message(), timeout=0.2)

    async def test_receive_maximum_holds_deliveries_until_ack(self, topic: str) -> None:  # noqa: ARG002
        """NanoMQ drops QoS 1 messages that exceed the client's Receive Maximum
        instead of queueing them until an acknowledgement frees the quota.
        """
        pytest.skip("NanoMQ drops messages beyond the client's Receive Maximum")


class TestNanoMQV311(BaseTestNanoMQ):
    host = "127.0.0.1"
    port = 1887
    version = "3.1.1"

    async def test_message_ordering(self, mqtt_client: MQTTClient, topic: str) -> None:
        payloads = [str(i).encode() for i in range(5)]
        async with mqtt_client.subscribe(topic, qos=QoS.AT_LEAST_ONCE) as sub:
            for payload in payloads:
                await mqtt_client.publish(topic, payload, qos=QoS.AT_LEAST_ONCE)
            received = [(await asyncio.wait_for(sub.get_message(), timeout=5.0)).payload for _ in payloads]

        assert Counter(received) == Counter(payloads), (received, payloads)
        if received != payloads:
            pytest.xfail(
                "NanoMQ 0.25.5 reorders MQTT 3.1.1 messages: "
                "https://github.com/faststream-community/zMQTT/actions/runs/36490218902"
            )


class TestNanoMQV5(BaseTestNanoMQ):
    host = "127.0.0.1"
    port = 1887
    version = "5.0"
