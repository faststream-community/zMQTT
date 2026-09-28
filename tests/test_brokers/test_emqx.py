import asyncio

import pytest

from tests.test_brokers._base import BrokerTestBase
from zmqtt import MQTTClient, QoS, Subscription
from zmqtt._internal.types.message import Message


class BaseTestEMQX(BrokerTestBase):
    @pytest.mark.parametrize("qos", [QoS.AT_LEAST_ONCE, QoS.EXACTLY_ONCE])
    async def test_detach_releases_read_loop_for_handler_reply(
        self,
        topic: str,
        qos: QoS,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # EMQX pipelines unacknowledged deliveries, so a handler can remain
        # active while another delivery blocks on the full subscription queue.
        async with (
            MQTTClient(self.host, self.port, version=self.version) as client,
            MQTTClient(self.host, self.port, version=self.version) as publisher,
        ):
            subscription = client.subscribe(topic, qos=qos, auto_ack=False, receive_buffer_size=1)
            blocked = asyncio.Event()
            put = subscription._queue.put

            async def observe_put(message: Message) -> None:
                if subscription._queue.full():
                    blocked.set()
                await put(message)

            monkeypatch.setattr(subscription._queue, "put", observe_put)
            await subscription.start()
            await publisher.publish(topic, b"active", qos=qos)
            active = await asyncio.wait_for(subscription.get_message(), timeout=5.0)
            await publisher.publish(topic, b"queued", qos=qos)
            await publisher.publish(topic, b"blocked", qos=qos)
            await asyncio.wait_for(blocked.wait(), timeout=5.0)

            await subscription.detach()
            await asyncio.wait_for(active.ack(), timeout=5.0)
            await asyncio.wait_for(
                client.publish(f"{topic}/response", b"done", qos=QoS.AT_LEAST_ONCE),
                timeout=5.0,
            )
            assert subscription._queue.empty()

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
