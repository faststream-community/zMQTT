"""Application routing contracts for incoming MQTT messages."""

from dataclasses import dataclass
from typing import Protocol

from zmqtt._internal.subscription_index import SubscriptionEntry
from zmqtt._internal.types.message import Message


class RequestClaim(Protocol):
    """An exclusively claimed request response awaiting delivery."""

    def deliver(self) -> None: ...


class RequestRouter(Protocol):
    """Select a pending request as the recipient for a message."""

    def claim(self, message: Message) -> RequestClaim | None: ...


@dataclass(slots=True)
class InboundRecipient:
    """The single application recipient selected for an incoming PUBLISH."""

    message: Message
    request: RequestClaim | None = None
    subscription: tuple[str, SubscriptionEntry] | None = None

    @property
    def auto_ack(self) -> bool:
        return self.request is not None or self.subscription is None or self.subscription[1].auto_ack
