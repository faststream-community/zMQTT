# Error Handling

## Exception hierarchy

```
MQTTError
  ├── MQTTConnectError      # CONNACK refused (return_code attribute)
  ├── MQTTProtocolError     # malformed or unexpected packet
  ├── MQTTDisconnectedError # connection lost unexpectedly
  └── MQTTTimeoutError      # ping or operation timed out
```

All exceptions are importable from `zmqtt.errors`:

```python
from zmqtt.errors import (
    MQTTError,
    MQTTConnectError,
    MQTTProtocolError,
    MQTTDisconnectedError,
    MQTTTimeoutError,
)
```

## When each is raised

### `MQTTConnectError`

Raised during `__aenter__` when the broker refuses the connection. The `return_code` attribute holds the CONNACK return code:

```python
from zmqtt.errors import MQTTConnectError

try:
    async with create_client("localhost") as client:
        ...
except MQTTConnectError as e:
    print(f"Broker refused connection: code {e.return_code}")
```

Common return codes (MQTT 3.1.1):

| Code | Meaning |
|------|---------|
| 1 | Unacceptable protocol version |
| 2 | Client identifier rejected |
| 3 | Server unavailable |
| 4 | Bad username or password |
| 5 | Not authorised |

### `MQTTProtocolError`

Raised when the broker sends a packet that violates the MQTT spec — wrong packet type in context, malformed header, etc. This usually indicates a broker bug or a mismatch between library version and broker behaviour.

### `MQTTDisconnectedError`

Raised when the TCP connection drops unexpectedly (network failure, broker restart, etc.). If reconnection is enabled (the default), this error is caught internally and the client reconnects automatically — your application code never sees it.

If reconnection is disabled (`ReconnectConfig(enabled=False)`), `MQTTDisconnectedError` propagates out of `publish()`, `subscribe()`, `ping()`, and through the context manager body.

### `MQTTTimeoutError`

Raised by `client.ping()` when no PINGRESP arrives within the timeout:

```python
try:
    rtt = await client.ping(timeout=5.0)
except MQTTTimeoutError:
    print("Broker not responding")
```

See [Manual Ping](advanced/ping.md) for the full `ping()` API.

## Reconnection interaction

When `ReconnectConfig(enabled=True)` (the default), `MQTTDisconnectedError` and `MQTTTimeoutError` inside the protocol loop are suppressed and the client reconnects with exponential backoff. Your `async for msg in sub` loop keeps waiting — it will resume delivering messages once the connection is restored.

When reconnection is disabled, the exception propagates out of the client context manager's `__aexit__` as an `ExceptionGroup` (Python 3.11+ TaskGroup semantics):

```python
from zmqtt import create_client, ReconnectConfig
from zmqtt.errors import MQTTDisconnectedError

async with create_client(
    "localhost",
    reconnect=ReconnectConfig(enabled=False),
) as client:
    try:
        async for msg in sub:
            ...
    except* MQTTDisconnectedError:
        print("Connection lost, reconnection disabled")
```

See [Reconnection](advanced/reconnection.md) for full details.

---

**See also:** [Connecting](connecting.md) · [Manual Ping](advanced/ping.md) · [Logging](logging.md)
