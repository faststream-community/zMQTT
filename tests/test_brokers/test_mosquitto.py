import asyncio
import re

import pytest

from tests.test_brokers._base import BrokerTestBase
from zmqtt import Subscription
from zmqtt._internal.types.qos import QoS
from zmqtt.client import MQTTClient
from zmqtt.errors import MQTTPublishError


class BaseTestMosquitto(BrokerTestBase):
    denied_topic = "zmqtt/e2e/denied"

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


class TestMosquittoV311(BaseTestMosquitto):
    host = "127.0.0.1"
    port = 1884
    version = "3.1.1"

    @pytest.mark.parametrize("qos", [QoS.AT_LEAST_ONCE, QoS.EXACTLY_ONCE])
    async def test_publish_in_denied_topic_mqtt311_does_not_raise(
        self,
        mqtt_client: MQTTClient,
        qos: QoS,
        topic: str,
    ) -> None:
        await mqtt_client.publish(self.denied_topic, b"denied", qos=qos)

        async with mqtt_client.subscribe(topic) as sub:
            await mqtt_client.publish(topic, b"payload-qos0")
            msg = await sub.get_message()

        assert msg.topic == topic
        assert msg.payload == b"payload-qos0"


class TestMosquittoV5(BaseTestMosquitto):
    host = "127.0.0.1"
    port = 1884
    version = "5.0"

    @pytest.mark.parametrize("qos", [QoS.AT_LEAST_ONCE, QoS.EXACTLY_ONCE])
    async def test_publish_in_denied_topic_raises_error(
        self,
        mqtt_client: MQTTClient,
        qos: QoS,
    ) -> None:
        with pytest.raises(
            MQTTPublishError, match=re.escape("Broker rejected publish (0x87 Not authorized)")
        ) as exc_info:
            await mqtt_client.publish(self.denied_topic, b"denied", qos=qos)

        assert exc_info.value.reason_code == 0x87
        assert exc_info.value.reason_name == "Not authorized"

    @pytest.mark.parametrize("qos", [QoS.AT_LEAST_ONCE, QoS.EXACTLY_ONCE])
    async def test_publish_in_denied_topic_remains_connection_usable(
        self,
        mqtt_client: MQTTClient,
        qos: QoS,
        topic: str,
    ) -> None:
        with pytest.raises(MQTTPublishError, match=re.escape("Broker rejected publish (0x87 Not authorized)")):
            await mqtt_client.publish(self.denied_topic, b"denied", qos=qos)

        async with mqtt_client.subscribe(topic) as sub:
            await mqtt_client.publish(topic, b"payload-qos0")
            msg = await sub.get_message()

        assert msg.topic == topic
        assert msg.payload == b"payload-qos0"
