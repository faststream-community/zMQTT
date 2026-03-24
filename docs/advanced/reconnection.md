# Reconnection

## Default behaviour

Reconnection is enabled by default. When the connection drops (`MQTTDisconnectedError` or `MQTTTimeoutError`), the client waits, reconnects, and re-subscribes automatically. Your `async for msg in sub` loop keeps waiting and resumes delivering messages once the connection is restored.

Your application code does not need to handle reconnection at all in the common case.

## `ReconnectConfig`

```python
from zmqtt import ReconnectConfig

config = ReconnectConfig(
    enabled=True,
    initial_delay=1.0,    # seconds before first retry
    max_delay=60.0,       # cap on retry interval
    backoff_factor=2.0,   # multiplier applied after each failure
)
```

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `True` | Enable/disable automatic reconnection |
| `initial_delay` | `1.0` | Seconds to wait before the first reconnect attempt |
| `max_delay` | `60.0` | Maximum delay between attempts |
| `backoff_factor` | `2.0` | Each failure multiplies the delay by this factor |

With the defaults: 1 s → 2 s → 4 s → 8 s → … → 60 s.

## Passing config to `create_client()`

```python
from zmqtt import create_client, ReconnectConfig

async with create_client(
    "localhost",
    reconnect=ReconnectConfig(initial_delay=0.5, max_delay=30.0),
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

With reconnection disabled, `MQTTDisconnectedError` propagates out of the client context manager as an `ExceptionGroup`. Catch it with `except*`:

```python
from zmqtt import MQTTDisconnectedError

try:
    async with create_client("localhost", reconnect=ReconnectConfig(enabled=False)) as client:
        async with client.subscribe("topic") as sub:
            async for msg in sub:
                ...
except* MQTTDisconnectedError:
    print("Connection lost")
```

---

**See also:** [Error Handling](../error-handling.md) · [Subscribing](../subscribing.md) · [Connecting](../connecting.md)
