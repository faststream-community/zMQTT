import pytest

from zmqtt import MQTTClient
from zmqtt._internal.packets.properties import PublishProperties
from zmqtt._internal.request_response import _RequestDispatcher
from zmqtt._internal.types.message import Message
from zmqtt._internal.types.qos import QoS


class FakeResponseSubscriptions:
    def __init__(self) -> None:
        self.added: list[str] = []
        self.removed: list[str] = []

    async def add_response_observer(self, topic: str) -> None:
        self.added.append(topic)

    async def remove_response_observer(self, topic: str) -> None:
        self.removed.append(topic)


async def test_request_raises_on_v311() -> None:
    client = MQTTClient("localhost", version="3.1.1")
    with pytest.raises(RuntimeError, match=r"MQTT 5.0"):
        await client.request("t", b"x")


def test_max_pending_requests_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_pending_requests"):
        MQTTClient("localhost", max_pending_requests=0)


async def test_response_claim_is_exclusive_and_connection_bound() -> None:
    dispatcher = _RequestDispatcher(max_pending_requests=1)
    first_connection = FakeResponseSubscriptions()
    dispatcher.bind(first_connection)
    pending = await dispatcher.register("responses", b"correlation")
    message = Message(
        topic="responses",
        payload=b"reply",
        qos=QoS.EXACTLY_ONCE,
        retain=False,
        properties=PublishProperties(correlation_data=b"correlation"),
    )

    stale_response = dispatcher.claim(message)
    assert stale_response is not None
    assert dispatcher.claim(message) is None

    second_connection = FakeResponseSubscriptions()
    dispatcher.bind(second_connection)
    await dispatcher.restore()
    current_response = dispatcher.claim(message)
    assert current_response is not None

    stale_response.deliver()
    assert not pending.future.done()
    current_response.deliver()
    assert await pending.future is message

    await pending.close()
    assert first_connection.added == ["responses"]
    assert second_connection.added == ["responses"]
    assert second_connection.removed == ["responses"]
