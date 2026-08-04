import asyncio

from tests.test_brokers._base import BrokerTestBase
from zmqtt import MQTTClient, QoS, Subscription


class BaseTestEMQX(BrokerTestBase):
    async def handle_sub_duplicates(
        self,
        *,
        sub: Subscription,
        n_duplicates: int,
    ) -> None:
        for _ in range(n_duplicates):
            await sub.get_message()
        assert sub._queue.empty()

    async def test_queue_prefixed_subscription_receives(self, topic: str) -> None:
        """A ``$queue/<filter>`` subscription (EMQX's group-less shared subscription) must
        receive messages published to the real topic.

        The broker strips the ``$queue/`` prefix and delivers on the bare topic, so the
        client must strip it too to match — otherwise every message is dropped as having
        no subscriber. This is how EMQX Message Queues are consumed.
        """
        bare = topic.lstrip("/")
        async with (
            MQTTClient(self.host, self.port, version=self.version) as client,
            client.subscribe(f"$queue/{bare}", qos=QoS.AT_LEAST_ONCE) as sub,
            MQTTClient(self.host, self.port, version=self.version) as publisher,
        ):
            for i in range(3):
                await publisher.publish(bare, f"s{i}".encode(), qos=QoS.AT_LEAST_ONCE)

            received = [await asyncio.wait_for(sub.get_message(), timeout=5.0) for _ in range(3)]

        assert {m.payload for m in received} == {b"s0", b"s1", b"s2"}


class TestEMQXV311(BaseTestEMQX):
    host = "127.0.0.1"
    port = 1888
    version = "3.1.1"


class TestEMQXV5(BaseTestEMQX):
    host = "127.0.0.1"
    port = 1888
    version = "5.0"
