# Reconnection

## Default behaviour

Reconnection is enabled by default and applies to both the **initial connection** and any subsequent connection loss. When a network-level error (`OSError`) occurs, the client waits and retries with exponential back-off. When a running connection drops (`MQTTDisconnectedError` or `MQTTTimeoutError`), it reconnects and re-subscribes automatically. Your `async for msg in sub` loop keeps waiting and resumes delivering messages once the connection is restored.

A broker refusal (`MQTTConnectError`, e.g. wrong credentials) is never retried — it propagates immediately regardless of `ReconnectConfig`.

Your application code does not need to handle reconnection at all in the common case.

## `ReconnectConfig`

```python
from zmqtt import ReconnectConfig

config = ReconnectConfig(
    enabled=True,
    initial_delay=1.0,          # seconds before first retry
    max_delay=60.0,             # cap on retry interval
    backoff_factor=2.0,         # multiplier applied after each failure
    max_attempts=5,   # None = retry indefinitely
)
```

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `True` | Enable/disable automatic reconnection |
| `initial_delay` | `1.0` | Seconds to wait before the first reconnect attempt |
| `max_delay` | `60.0` | Maximum delay between attempts |
| `backoff_factor` | `2.0` | Each failure multiplies the delay by this factor |
| `max_attempts` | `5` | Total connection attempts before giving up. `None` retries indefinitely |

With the default limit of five total attempts, failed attempts are separated by
delays of 1 s, 2 s, 4 s, and 8 s. With `max_attempts=None`, retries continue and
the delay eventually caps at 60 s.

## Passing config to `create_client()`

```python
from zmqtt import create_client, ReconnectConfig

async with create_client(
    "localhost",
    reconnect=ReconnectConfig(initial_delay=0.5, max_delay=30.0, max_attempts=None),
) as client:
    ...
```

## Handling failed connection recovery

Use `on_connection_recovery_failed` when application code needs to observe that
a running client cannot restore its connection:

```python
from zmqtt import ReconnectConfig, create_client


async def connection_recovery_failed() -> None:
    print("MQTT connection could not be restored")


async with create_client(
    "localhost",
    reconnect=ReconnectConfig(max_attempts=5),
    on_connection_recovery_failed=connection_recovery_failed,
) as client:
    ...
```

The async callback is awaited exactly once after a previously established
connection cannot be restored. It is not called when the initial connection
fails or when the client is disconnected cleanly. Initial connection failures
still propagate from `connect()` or the async context manager entry.

After the callback returns, the client stops reconnecting and the terminal
connection error is propagated to active subscription iterators. Use the
callback to notify the component that owns the client lifecycle when it needs
to take further action.

## How subscriptions survive reconnect

Each `Subscription` is re-subscribed on the new connection automatically. The local message queue is preserved — messages that arrived before the disconnect are still in the queue and will be delivered to your code. New messages start flowing once the broker confirms the re-subscribe.

This preserves the local subscription lifecycle, not every message published
while the client was offline. Delivery during the gap depends on QoS and the
broker-side session; durable redelivery requires the persistent-session settings
described in [Manual Acknowledgement](manual-ack.md#connection-loss-before-ack).

## Disabling reconnection

```python
from zmqtt import create_client, ReconnectConfig

async with create_client(
    "localhost",
    reconnect=ReconnectConfig(enabled=False),
) as client:
    ...
```

With reconnection disabled, the client stops on the first connection loss and
invokes `on_connection_recovery_failed`, if configured. `MQTTDisconnectedError`
is raised on the next call to `publish()`, `ping()`, entering a new `subscribe()`
context, or waiting for a message from an active subscription.

---

**See also:** [Error Handling](../error-handling.md) · [Subscribing](../subscribing.md) · [Connecting](../connecting.md)
