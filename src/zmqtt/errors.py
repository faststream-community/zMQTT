class MQTTError(Exception):
    """Base class for all zmqtt exceptions."""


class MQTTConnectError(MQTTError):
    """CONNACK returned a non-zero return code."""

    def __init__(self, return_code: int) -> None:
        self.return_code = return_code
        super().__init__(f"Connection refused: return code {return_code}")


class MQTTProtocolError(MQTTError):
    """Unexpected or malformed packet received."""


class MQTTDisconnectedError(MQTTError):
    """Connection lost unexpectedly."""


class MQTTTimeoutError(MQTTError):
    """An MQTT operation did not complete within the allotted time."""


class MQTTInvalidTopicError(MQTTError):
    """Topic string or topic filter failed MQTT validation."""
