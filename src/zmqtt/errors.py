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


class MQTTSubscribeError(MQTTError):
    """The broker rejected one or more filters in a SUBSCRIBE (SUBACK >= 0x80).

    Most commonly an authorization denial: without this error the subscription
    looks successful and silently never receives anything.
    """

    def __init__(self, failures: dict[str, int]) -> None:
        self.failures = failures
        rendered = ", ".join(f"{f!r} (0x{code:02X})" for f, code in failures.items())
        super().__init__(f"Broker rejected subscription: {rendered}")


class MQTTInvalidTopicError(MQTTError):
    """Topic string or topic filter failed MQTT validation."""
