"""Unit tests for MQTTClient construction and connect-retry behaviour."""

import pytest

from zmqtt import MQTTClient, create_client


def test_connect_timeout_default_is_30s() -> None:
    client = MQTTClient("localhost")
    assert client._connect_timeout == 30.0


@pytest.mark.parametrize("bad", [0, -1, -0.5, float("nan")])
def test_non_positive_connect_timeout_raises(bad: float) -> None:
    with pytest.raises(ValueError, match="connect_timeout must be positive"):
        create_client("localhost", connect_timeout=bad)
