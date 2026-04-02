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

With the defaults: 1 s → 2 s → 4 s → 8 s → … → 60 s, up to 5 total attempts.

## Passing config to `create_client()`

```python
from zmqtt import create_client, ReconnectConfig

async with create_client(
    "localhost",
    reconnect=ReconnectConfig(initial_delay=0.5, max_delay=30.0, max_attempts=None),
) as client:
    ...
```

## How subscriptions survive reconnect

Each `Subscription` is re-subscribed on the new connection automatically. The local message queue is preserved — messages that arrived before the disconnect are still in the queue and will be delivered to your code. New messages start flowing once the broker confirms the re-subscribe.

## Disabling reconnection

```python
from zmqtt import create_client, ReconnectConfig

async with create_client(
    "localhost",
    reconnect=ReconnectConfig(enabled=False),
) as client:
    ...
```

With reconnection disabled, the client stops on the first connection loss. `MQTTDisconnectedError` is raised on the next call to `publish()`, `ping()`, or entering a new `subscribe()` context. An active `async for msg in sub` loop will hang once the connection drops — no further messages are delivered and no exception is raised from the iterator itself.

---

**See also:** [Error Handling](../error-handling.md) · [Subscribing](../subscribing.md) · [Connecting](../connecting.md)
