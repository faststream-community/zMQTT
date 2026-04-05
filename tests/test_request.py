import pytest

from zmqtt import MQTTClient


async def test_request_raises_on_v311() -> None:
    client = MQTTClient("localhost", version="3.1.1")
    with pytest.raises(RuntimeError, match=r"MQTT 5.0"):
        await client.request("t", b"x")
