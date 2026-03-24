from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from zmqtt._internal.packets.properties import PublishProperties
from zmqtt._internal.types.qos import QoS


@dataclass(slots=True, kw_only=True)
class Message:
    """Incoming MQTT message as delivered to application code.

    Attributes:
        topic: The topic on which the message was published.
        payload: Raw message body as bytes.
        qos: QoS level at which the message was delivered.
        retain: ``True`` if the broker sent this as a retained message.
        properties: MQTT 5.0 publish properties, or ``None`` for MQTT 3.1.1
            connections.
    """

    topic: str
    payload: bytes
    qos: QoS
    retain: bool
    properties: PublishProperties | None = None  # v5 only
    _ack_callback: Callable[[], Awaitable[None]] | None = field(
        default=None,
        repr=False,
        init=False,
    )
    _resolved: bool = field(default=False, repr=False, init=False)

    async def ack(self) -> None:
        """
        Send the protocol-level ack for this message.
        Idempotent; no-op when auto_ack=True.
        """
        if self._resolved:
            return
        object.__setattr__(self, "_resolved", True)
        if self._ack_callback is not None:
            await self._ack_callback()
