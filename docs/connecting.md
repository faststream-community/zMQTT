# Connecting

## `create_client()`

`create_client()` is the preferred entry point. It returns a version-typed
client object and accepts the public connection and routing parameters:

```python
from zmqtt import create_client

client = create_client(
    host="localhost",
    port=1883,
    client_id="my-app",
    keepalive=60,
    clean_session=True,
    username="user",
    password="secret",
)
```

`host` and `port` may be positional; the remaining parameters are keyword-only
and have sensible defaults.

## Last Will

Configure the message the broker publishes when the client connection closes
unexpectedly with `Will`:

```python
from zmqtt import QoS, Will, create_client

client = create_client(
    "broker.example.com",
    will=Will(
        topic="devices/device-42/status",
        payload=b"offline",
        qos=QoS.AT_LEAST_ONCE,
        retain=True,
    ),
)
```

MQTT 5.0 clients can attach `WillProperties`:

```python
from zmqtt import QoS, Will, WillProperties, create_client

client = create_client(
    "broker.example.com",
    version="5.0",
    will=Will(
        topic="devices/device-42/status",
        payload=b"offline",
        qos=QoS.AT_LEAST_ONCE,
        retain=True,
        properties=WillProperties(
            will_delay_interval=10,
            content_type="text/plain",
        ),
    ),
)
```

The Will configuration is reused on automatic reconnection. Supplying
`WillProperties` to an MQTT 3.1.1 client raises `RuntimeError`.

## Version selection

Pass `version="3.1.1"` (default) or `version="5.0"`:

```python
client_v311 = create_client("localhost", version="3.1.1")
client_v5   = create_client("localhost", version="5.0")
```

The return type reflects the version — `MQTTClientV311` or `MQTTClientV5` — so your type checker can catch version-specific API misuse (e.g. using `PublishProperties` on a 3.1.1 connection). See [MQTT 5.0](advanced/mqtt5.md) for 5.0-specific features.

## TLS

| Value | Behaviour |
|-------|-----------|
| `tls=False` (default) | Plain TCP |
| `tls=True` | TLS with the system CA bundle |
| `tls=ssl.SSLContext` | TLS with a custom context |

```python
import ssl

# System CA — validates the broker's certificate automatically
async with create_client("broker.example.com", port=8883, tls=True) as client:
    ...

# Custom CA (self-signed broker)
ctx = ssl.create_default_context(cafile="/path/to/ca.pem")
async with create_client("broker.example.com", port=8883, tls=ctx) as client:
    ...
```

## Connection parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `host` | — | Broker hostname or IP |
| `port` | `1883` | Broker port (use `8883` for TLS) |
| `client_id` | `""` | Client identifier; empty string = broker-assigned |
| `keepalive` | `60` | Keepalive interval in seconds |
| `clean_session` | `True` | Discard broker-side session on connect |
| `username` | `None` | MQTT username |
| `password` | `None` | MQTT password |
| `will` | `None` | Last Will published after an unexpected connection loss |
| `tls` | `False` | TLS configuration (see above) |
| `reconnect` | `ReconnectConfig()` | Reconnection behaviour — see [Reconnection](advanced/reconnection.md) |
| `on_connection_recovery_failed` | `None` | Async callback invoked once when a running connection cannot be restored |
| `mqtt_connect_timeout` | `30.0` | Seconds to wait for the broker's CONNACK before raising `MQTTTimeoutError` (must be `> 0`). Treated as retryable when reconnection is enabled. |
| `transport_factory` | `None` | Optional low-level transport override, primarily for testing |
| `session_expiry_interval` | `0` | MQTT 5.0 session expiry in seconds (ignored on 3.1.1) |
| `stripped_prefixes` | `("$queue", "$exclusive")` | Broker subscription prefixes removed before local topic matching; `$share/<group>/` is always supported |
| `max_pending_requests` | `1000` | Maximum concurrent MQTT 5 `request()` calls; additional calls wait before publishing |
| `version` | `"3.1.1"` | Protocol version: `"3.1.1"` or `"5.0"` |

See [Reconnection](advanced/reconnection.md) for `ReconnectConfig`,
[Scaling](advanced/scaling.md) for subscription-prefix matching, and
[Request / Response](advanced/request-response.md) for request concurrency.
[Error Handling](error-handling.md) covers `MQTTConnectError` on refused
connections.

## Context manager lifecycle

`create_client()` returns a client object but does **not** connect immediately. Connection happens on `__aenter__`:

```python
async with create_client("localhost") as client:
    # Connected — protocol handshake complete
    await client.publish("test", "hello")
# Disconnected — DISCONNECT sent, socket closed
```

## Manual lifecycle

When the context manager pattern does not fit your program structure — for example in framework startup/shutdown hooks — use `connect()` and `disconnect()` directly:

```python
client = create_client("localhost")
await client.connect()

await client.publish("test", "hello")

await client.disconnect()
```

`disconnect()` is safe to call even if the connection has already been lost.

## `MQTTClientV311` / `MQTTClientV5` Protocol types

`create_client()` returns a `Protocol` view of the concrete `MQTTClient`. This means:

- Mypy knows that `version="5.0"` clients have `auth()` and accept `PublishProperties`.
- Mypy knows that `version="3.1.1"` clients do not.
- The underlying object is always `MQTTClient` — no two separate implementations.

```python
from zmqtt import create_client, MQTTClientV5
from zmqtt import PublishProperties

async def send_with_expiry(client: MQTTClientV5) -> None:
    props = PublishProperties(message_expiry_interval=60)
    await client.publish("data", b"payload", properties=props)
```
