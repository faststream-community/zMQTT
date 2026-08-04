import pytest

from zmqtt import MQTTClient


async def test_request_raises_on_v311() -> None:
    client = MQTTClient("localhost", version="3.1.1")
    with pytest.raises(RuntimeError, match=r"MQTT 5.0"):
        await client.request("t", b"x")


def test_max_pending_requests_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_pending_requests"):
        MQTTClient("localhost", max_pending_requests=0)
