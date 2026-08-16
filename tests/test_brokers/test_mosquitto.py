import asyncio
import uuid

import pytest

from tests.test_brokers._base import BrokerTestBase
from zmqtt import (
    MQTTClient,
    MQTTDisconnectedError,
    MQTTTimeoutError,
    QoS,
    ReconnectConfig,
    Subscription,
    Will,
    WillProperties,
)


class BaseTestMosquitto(BrokerTestBase):
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

    async def test_last_will_survives_reconnect(self, topic: str) -> None:
        will_topic = f"{topic}/will"
        client_id = f"zmqtt-will-{uuid.uuid4().hex[:8]}"
        will_properties = WillProperties(content_type="text/plain") if self.version == "5.0" else None
        will = Will(
            topic=will_topic,
            payload=b"offline",
            qos=QoS.AT_LEAST_ONCE,
            retain=True,
            properties=will_properties,
        )
        reconnect = ReconnectConfig(enabled=True, initial_delay=0.5, max_delay=0.5, max_attempts=None)

        async with (
            MQTTClient(self.host, self.port, version=self.version) as observer,
            observer.subscribe(will_topic, qos=QoS.AT_LEAST_ONCE) as subscription,
            MQTTClient(
                self.host,
                self.port,
                client_id=client_id,
                reconnect=reconnect,
                version=self.version,
                will=will,
            ) as client,
        ):

            async def connection_restored() -> bool:
                try:
                    await client.ping(timeout=0.2)
                except (MQTTDisconnectedError, MQTTTimeoutError, OSError):
                    return False
                return True

            async def wait_for_reconnect() -> None:
                deadline = asyncio.get_running_loop().time() + 5.0
                while not await connection_restored():
                    if asyncio.get_running_loop().time() >= deadline:
                        pytest.fail("Client did not reconnect within 5 s")
                    await asyncio.sleep(0.05)

            await self.trigger_session_takeover(client_id=client_id)
            first = await asyncio.wait_for(subscription.get_message(), timeout=5.0)
            await wait_for_reconnect()

            await self.trigger_session_takeover(client_id=client_id)
            second = await asyncio.wait_for(subscription.get_message(), timeout=5.0)
            await wait_for_reconnect()

        for message in (first, second):
            assert message.topic == will_topic
            assert message.payload == b"offline"
            assert message.qos is QoS.AT_LEAST_ONCE
            if self.version == "5.0":
                assert message.properties is not None
                assert message.properties.content_type == "text/plain"
            else:
                assert message.properties is None

        async with MQTTClient(self.host, self.port, version=self.version) as observer:
            async with observer.subscribe(will_topic, qos=QoS.AT_LEAST_ONCE) as subscription:
                retained = await asyncio.wait_for(subscription.get_message(), timeout=5.0)
            await observer.publish(will_topic, b"", qos=QoS.AT_LEAST_ONCE, retain=True)

        assert retained.retain is True
        if self.version == "5.0":
            assert retained.properties is not None
            assert retained.properties.content_type == "text/plain"
        else:
            assert retained.properties is None


class TestMosquittoV311(BaseTestMosquitto):
    host = "127.0.0.1"
    port = 1884
    version = "3.1.1"


class TestMosquittoV5(BaseTestMosquitto):
    host = "127.0.0.1"
    port = 1884
    version = "5.0"
