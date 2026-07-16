import asyncio
import uuid

import pytest

from tests.test_brokers._base import BrokerTestBase
from zmqtt import MQTTClient, QoS, Subscription


class BaseTestArtemis(BrokerTestBase):
    async def handle_sub_duplicates(
        self,
        *,
        sub: Subscription,
        n_duplicates: int,
    ) -> None:
        for _ in range(n_duplicates):
            await sub.get_message()
        assert sub._queue.empty()

    async def test_subscription_identifier_overlapping(self, topic: str) -> None:
        """Artemis supports subscription identifiers (a lone identified
        subscription gets its echo just fine) — what it does NOT share with the
        other brokers is the two-copies-per-overlap delivery: for a ``$share``
        subscription plus its plain twin it sends ONE copy per message, to the
        plain subscription (tagged with its id). The shared twin starves, so
        waiting on it must time out.
        """
        if self.version != "5.0":  # 3.1.1 refuses the parameter — same as everywhere
            await super().test_subscription_identifier_overlapping(topic)
            return

        bare_topic = topic.lstrip("/")
        group = f"zmqtt-si-{uuid.uuid4().hex[:8]}"

        async with (
            MQTTClient(self.host, self.port, version=self.version) as client,
            client.subscribe(
                f"$share/{group}/{bare_topic}",
                qos=QoS.AT_LEAST_ONCE,
                subscription_identifier=1,
            ) as shared,
            client.subscribe(bare_topic, qos=QoS.AT_LEAST_ONCE, subscription_identifier=2) as plain,
            MQTTClient(self.host, self.port, version=self.version) as publisher,
        ):
            for i in range(3):
                await publisher.publish(bare_topic, f"m{i}".encode(), qos=QoS.AT_LEAST_ONCE)

            plain_msgs = [await asyncio.wait_for(plain.get_message(), timeout=5.0) for _ in range(3)]
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(shared.get_message(), timeout=1.0)

        assert [m.payload for m in plain_msgs] == [b"m0", b"m1", b"m2"]
        assert {m.properties.subscription_identifier for m in plain_msgs if m.properties} == {2}


class TestArtemisV311(BaseTestArtemis):
    host = "127.0.0.1"
    port = 1883
    version = "3.1.1"


class TestArtemisV5(BaseTestArtemis):
    host = "127.0.0.1"
    port = 1883
    version = "5.0"
