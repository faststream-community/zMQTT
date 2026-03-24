from enum import IntEnum


class RetainHandling(IntEnum):
    """
    MQTT 5.0 subscription option controlling which retained messages are delivered.
    """

    SEND_ON_SUBSCRIBE = 0
    SEND_IF_NOT_EXISTS = 1
    DO_NOT_SEND = 2
