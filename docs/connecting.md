# Connecting

## `create_client()`

`create_client()` is the preferred entry point. It returns a typed client object and accepts all connection parameters:

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

All parameters except `host` are keyword-only and have sensible defaults.

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
| `tls` | `False` | TLS configuration (see above) |
| `reconnect` | `ReconnectConfig()` | Reconnection behaviour — see [Reconnection](advanced/reconnection.md) |
| `session_expiry_interval` | `0` | MQTT 5.0 session expiry in seconds (ignored on 3.1.1) |
| `version` | `"3.1.1"` | Protocol version: `"3.1.1"` or `"5.0"` |

See [Reconnection](advanced/reconnection.md) for `ReconnectConfig` options and [Error Handling](error-handling.md) for `MQTTConnectError` on refused connections.

## Context manager lifecycle

`create_client()` returns a client object but does **not** connect immediately. Connection happens on `__aenter__`:

```python
async with create_client("localhost") as client:
    # Connected — protocol handshake complete
    await client.publish("test", "hello")
# Disconnected — DISCONNECT sent, socket closed
```

You can also manage the lifecycle manually (less common):

```python
client = create_client("localhost")
async with client:
    await client.publish("test", "hello")
```

## `MQTTClientV311` / `MQTTClientV5` Protocol types

`create_client()` returns a `Protocol` view of the concrete `MQTTClient`. This means:

- Mypy knows that `version="5.0"` clients have `auth()` and accept `PublishProperties`.
- Mypy knows that `version="3.1.1"` clients do not.
- The underlying object is always `MQTTClient` — no two separate implementations.

```python
from zmqtt import create_client, MQTTClientV5
from zmqtt.packets.properties import PublishProperties

async def send_with_expiry(client: MQTTClientV5) -> None:
    props = PublishProperties(message_expiry_interval=60)
    await client.publish("data", b"payload", properties=props)
```
