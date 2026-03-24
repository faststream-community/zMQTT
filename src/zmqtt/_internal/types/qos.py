from enum import IntEnum


class QoS(IntEnum):
    """MQTT Quality of Service delivery guarantee levels.

    Attributes:
        AT_MOST_ONCE: Fire-and-forget. No acknowledgement, no retries (QoS 0).
        AT_LEAST_ONCE: Acknowledged delivery. The message may arrive more than
            once (QoS 1).
        EXACTLY_ONCE: Four-way handshake guarantees exactly-once delivery (QoS 2).
    """

    AT_MOST_ONCE = 0
    AT_LEAST_ONCE = 1
    EXACTLY_ONCE = 2
