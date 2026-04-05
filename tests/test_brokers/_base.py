"""Base class for E2E broker tests.

Subclasses set host/port/version and get all tests for free.
Run with:  pytest -m broker
"""

import abc
import asyncio
import contextlib
import uuid
from collections.abc import AsyncGenerator
from typing import ClassVar, Literal

import pytest

from zmqtt import MQTTClient, QoS, ReconnectConfig, Subscription
from zmqtt._internal.packets.properties import PublishProperties


@pytest.mark.broker
class BrokerTestBase(abc.ABC):
    host: ClassVar[str] = "127.0.0.1"
    port: ClassVar[int] = 1883
    version: ClassVar[Literal["3.1.1", "5.0"]] = "3.1.1"

    @abc.abstractmethod
    async def handle_sub_duplicates(
        self,
        *,
        sub: Subscription,
        n_duplicates: int,
    ) -> None: ...

    @pytest.fixture
    async def mqtt_client(self) -> AsyncGenerator[MQTTClient]:
        async with MQTTClient(
            self.host,
            self.port,
            client_id=f"zmqtt-test-{uuid.uuid4().hex[:8]}",
            version=self.version,
        ) as client:
            yield client

    async def test_ping(self, mqtt_client: MQTTClient) -> None:
        await mqtt_client.ping()

    async def test_publish_qos0(self, mqtt_client: MQTTClient, topic: str) -> None:
        await mqtt_client.publish(topic, b"hello")

    async def test_publish_qos1(self, mqtt_client: MQTTClient, topic: str) -> None:
        await mqtt_client.publish(topic, b"hello", qos=QoS.AT_LEAST_ONCE)

    async def test_publish_qos2(self, mqtt_client: MQTTClient, topic: str) -> None:
        await mqtt_client.publish(topic, b"hello", qos=QoS.EXACTLY_ONCE)

    async def test_subscribe_receive_qos0(
        self,
        mqtt_client: MQTTClient,
        topic: str,
    ) -> None:
        async with mqtt_client.subscribe(topic) as sub:
            await mqtt_client.publish(topic, b"payload-qos0")
            msg = await sub.get_message()

        assert msg.topic == topic
        assert msg.payload == b"payload-qos0"

    async def test_subscribe_receive_qos1(
        self,
        mqtt_client: MQTTClient,
        topic: str,
    ) -> None:
        async with mqtt_client.subscribe(topic, qos=QoS.AT_LEAST_ONCE) as sub:
            await mqtt_client.publish(topic, b"payload-qos1", qos=QoS.AT_LEAST_ONCE)
            msg = await asyncio.wait_for(sub.get_message(), timeout=5.0)
        assert msg.payload == b"payload-qos1"

    async def test_subscribe_receive_qos2(
        self,
        mqtt_client: MQTTClient,
        topic: str,
    ) -> None:
        async with mqtt_client.subscribe(topic, qos=QoS.EXACTLY_ONCE) as sub:
            await mqtt_client.publish(topic, b"payload-qos2", qos=QoS.EXACTLY_ONCE)
            msg = await asyncio.wait_for(sub.get_message(), timeout=5.0)

        assert msg.payload == b"payload-qos2"

    async def test_subscribe_wildcard(
        self,
        mqtt_client: MQTTClient,
        topic: str,
    ) -> None:
        prefix = f"{topic}/wild"
        async with mqtt_client.subscribe(f"{prefix}/#") as sub:
            await mqtt_client.publish(f"{prefix}/a/b", b"w1")
            await mqtt_client.publish(f"{prefix}/c", b"w2")
            msgs = [await asyncio.wait_for(sub.get_message(), timeout=5.0) for _ in range(2)]

        assert {m.payload for m in msgs} == {b"w1", b"w2"}
        assert all(m.topic.startswith(prefix) for m in msgs)

    async def test_message_ordering(self, mqtt_client: MQTTClient, topic: str) -> None:
        payloads = [str(i).encode() for i in range(5)]
        async with mqtt_client.subscribe(topic, qos=QoS.AT_LEAST_ONCE) as sub:
            for p in payloads:
                await mqtt_client.publish(topic, p, qos=QoS.AT_LEAST_ONCE)
            received = [(await asyncio.wait_for(sub.get_message(), timeout=5.0)).payload for _ in payloads]

        assert received == payloads

    async def test_shared_subscription(self, topic: str) -> None:
        group = f"zmqtt-sh-{uuid.uuid4().hex[:8]}"
        # Strip leading slash to avoid $share/group//topic (double slash) which some brokers reject
        bare_topic = topic.lstrip("/")
        shared_filter = f"$share/{group}/{bare_topic}"

        async with (
            MQTTClient(self.host, self.port, version=self.version) as c1,
            MQTTClient(self.host, self.port, version=self.version) as c2,
            MQTTClient(
                self.host, self.port, client_id=f"zmqtt-pub-{uuid.uuid4().hex[:8]}", version=self.version
            ) as pub,
            c1.subscribe(shared_filter, qos=QoS.AT_LEAST_ONCE) as sub1,
            c2.subscribe(shared_filter, qos=QoS.AT_LEAST_ONCE) as sub2,
        ):
            for i in range(10):
                await pub.publish(bare_topic, f"msg-{i}".encode(), qos=QoS.AT_LEAST_ONCE)

            remain_msgs = 10
            e = asyncio.Event()

            async def drain(sub: Subscription) -> None:
                nonlocal remain_msgs
                async for _ in sub:
                    remain_msgs -= 1
                    if remain_msgs < 1:
                        e.set()

            task1 = asyncio.create_task(drain(sub1))
            task2 = asyncio.create_task(drain(sub2))
            await asyncio.wait_for(e.wait(), timeout=5.0)

            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(sub1.get_message(), timeout=0.2)

            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(sub2.get_message(), timeout=0.2)
            task1.cancel()
            task2.cancel()

    async def test_reconnect_subscription_survives(self, topic: str) -> None:
        reconnect = ReconnectConfig(enabled=True, initial_delay=0.1, max_delay=1.0)

        async with (
            MQTTClient(
                self.host,
                self.port,
                client_id=f"zmqtt-reconnect-{uuid.uuid4().hex[:8]}",
                reconnect=reconnect,
                version=self.version,
            ) as client,
            client.subscribe(topic) as sub,
        ):
            assert client._protocol is not None
            await client._protocol._transport.close()

            for _ in range(100):
                await asyncio.sleep(0.05)
                if client._protocol is not None and client._protocol._transport.is_connected:
                    break
            else:
                pytest.fail("Client did not reconnect within 5 s")

            await client.publish(topic, b"after-reconnect")
            msg = await asyncio.wait_for(sub.get_message(), timeout=5.0)

        assert msg.payload == b"after-reconnect"

    async def test_overlapping_wildcard_priority_routing(
        self,
        mqtt_client: MQTTClient,
        topic: str,
    ) -> None:
        async with mqtt_client.subscribe(f"{topic}/#", f"{topic}/exact") as sub:
            await mqtt_client.publish(f"{topic}/exact", b"hit-exact")
            msg1 = await asyncio.wait_for(sub.get_message(), timeout=5.0)
            assert msg1.payload == b"hit-exact"
            await self.handle_sub_duplicates(sub=sub, n_duplicates=1)

            await mqtt_client.publish(f"{topic}/other", b"hit-wildcard")
            msg2 = await asyncio.wait_for(sub.get_message(), timeout=5.0)
            assert msg2.payload == b"hit-wildcard"
            assert sub._queue.empty()

    async def test_overlapping_wildcard_plus_over_hash(
        self,
        mqtt_client: MQTTClient,
        topic: str,
    ) -> None:
        async with mqtt_client.subscribe(f"{topic}/+/c", f"{topic}/#") as sub:
            await mqtt_client.publish(f"{topic}/b/c", b"plus-wins")
            msg = await asyncio.wait_for(sub.get_message(), timeout=5.0)
            assert msg.payload == b"plus-wins"
            await self.handle_sub_duplicates(sub=sub, n_duplicates=1)

    async def test_overlapping_wildcard_three_way(
        self,
        mqtt_client: MQTTClient,
        topic: str,
    ) -> None:
        async with mqtt_client.subscribe(
            f"{topic}/b/c",
            f"{topic}/b/+",
            f"{topic}/#",
        ) as sub:
            await mqtt_client.publish(f"{topic}/b/c", b"exact-wins")
            msg = await asyncio.wait_for(sub.get_message(), timeout=5.0)
            assert msg.payload == b"exact-wins"
            await self.handle_sub_duplicates(sub=sub, n_duplicates=2)

    async def test_wildcard_hash_matches_bare_topic(
        self,
        mqtt_client: MQTTClient,
        topic: str,
    ) -> None:
        async with mqtt_client.subscribe(f"{topic}/+", f"{topic}/#") as sub:
            await mqtt_client.publish(topic, b"bare-topic")
            msg = await asyncio.wait_for(sub.get_message(), timeout=5.0)
            assert msg.payload == b"bare-topic"
            await asyncio.sleep(0.1)
            assert sub._queue.empty()

    async def test_manual_ack_qos1(self, mqtt_client: MQTTClient, topic: str) -> None:
        async with mqtt_client.subscribe(
            topic,
            qos=QoS.AT_LEAST_ONCE,
            auto_ack=False,
        ) as sub:
            await mqtt_client.publish(topic, b"ack-me", qos=QoS.AT_LEAST_ONCE)
            msg = await asyncio.wait_for(sub.get_message(), timeout=5.0)
            await msg.ack()

        assert msg.payload == b"ack-me"

    async def test_manual_ack_qos1_idempotent(
        self,
        mqtt_client: MQTTClient,
        topic: str,
    ) -> None:
        async with mqtt_client.subscribe(
            topic,
            qos=QoS.AT_LEAST_ONCE,
            auto_ack=False,
        ) as sub:
            await mqtt_client.publish(topic, b"ack-twice", qos=QoS.AT_LEAST_ONCE)
            msg = await asyncio.wait_for(sub.get_message(), timeout=5.0)
            await msg.ack()
            await msg.ack()

        assert msg.payload == b"ack-twice"

    async def test_manual_connect_disconnect(self) -> None:
        client = MQTTClient(
            self.host,
            self.port,
            client_id=f"zmqtt-manual-{uuid.uuid4().hex[:8]}",
            version=self.version,
        )
        await client.connect()
        try:
            rtt = await client.ping()
            assert rtt >= 0
        finally:
            await client.disconnect()

    async def test_context_manager_manual_pub_sub(self, topic: str) -> None:
        async with MQTTClient(
            self.host,
            self.port,
            client_id=f"zmqtt-manual-ps-{uuid.uuid4().hex[:8]}",
            version=self.version,
        ) as client:
            sub = client.subscribe(topic, qos=QoS.AT_LEAST_ONCE)
            await sub.start()
            try:
                await client.publish(topic, b"manual-pubsub", qos=QoS.AT_LEAST_ONCE)
                msg = await asyncio.wait_for(sub.get_message(), timeout=5.0)
            finally:
                await sub.stop()

        assert msg.payload == b"manual-pubsub"

    async def test_manual_ack_qos2(self, mqtt_client: MQTTClient, topic: str) -> None:
        async with mqtt_client.subscribe(
            topic,
            qos=QoS.EXACTLY_ONCE,
            auto_ack=False,
        ) as sub:
            await mqtt_client.publish(topic, b"ack-qos2", qos=QoS.EXACTLY_ONCE)
            msg = await asyncio.wait_for(sub.get_message(), timeout=5.0)
            await msg.ack()

        assert msg.payload == b"ack-qos2"

    async def test_request_response(self, topic: str) -> None:
        if self.version != "5.0":
            return

        async with (
            MQTTClient(self.host, self.port, version=self.version) as requester,
            MQTTClient(self.host, self.port, version=self.version) as responder,
            responder.subscribe(topic) as req_sub,
        ):

            async def respond() -> None:
                msg = await asyncio.wait_for(req_sub.get_message(), timeout=5.0)
                assert msg.properties is not None
                assert msg.properties.response_topic is not None
                await responder.publish(
                    msg.properties.response_topic,
                    b"pong",
                    properties=PublishProperties(
                        correlation_data=msg.properties.correlation_data,
                    ),
                )

            responder_task = asyncio.create_task(respond())
            reply = await requester.request(topic, b"ping", timeout=5.0)
            await responder_task

        assert reply.payload == b"pong"
        assert reply.properties is not None
        assert reply.properties.correlation_data is not None

    async def test_request_timeout(self, topic: str) -> None:
        if self.version != "5.0":
            return

        async with MQTTClient(self.host, self.port, version=self.version) as client:
            with pytest.raises(asyncio.TimeoutError):
                await client.request(topic + "/nobody-listening", b"ping", timeout=0.3)

    async def test_request_reply_topic_unsubscribed_after_timeout(self, topic: str) -> None:
        if self.version != "5.0":
            return

        async with MQTTClient(self.host, self.port, version=self.version) as client:
            sub_count_before = len(client._subscriptions)
            with contextlib.suppress(asyncio.TimeoutError):
                await client.request(topic + "/void", b"x", timeout=0.1)
            assert len(client._subscriptions) == sub_count_before
