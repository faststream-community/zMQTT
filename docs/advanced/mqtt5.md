# MQTT 5.0

## Enabling MQTT 5.0

Pass `version="5.0"` to `create_client()` (see [Version selection](../connecting.md#version-selection)):

```python
from zmqtt import create_client

async with create_client("localhost", version="5.0") as client:
    ...
```

The return type is `MQTTClientV5`, which exposes 5.0-specific methods and accepts 5.0-specific parameters.

## Session expiry interval

Controls how long the broker preserves your session after disconnect. `0` (default) means the session ends immediately on disconnect; `0xFFFFFFFF` means the session never expires.

```python
async with create_client("localhost", version="5.0", session_expiry_interval=3600) as client:
    # Session survives for 1 hour after disconnect
    ...
```

## Publish properties

`PublishProperties` can be attached to any `publish()` call on a 5.0 connection:

```python
from zmqtt.packets.properties import PublishProperties

props = PublishProperties(
    message_expiry_interval=300,       # broker discards after 300 s
    content_type="application/json",
    response_topic="replies/my-app",
    correlation_data=b"request-id-42",
    user_properties=(("x-source", "sensor-01"), ("x-region", "eu-west")),
)
await client.publish("data/readings", b'{"temp": 23.4}', properties=props)
```

Received messages expose properties via `msg.properties` (a `PublishProperties` instance or `None`):

```python
async for msg in sub:
    if msg.properties and msg.properties.response_topic:
        await client.publish(
            msg.properties.response_topic,
            b"ok",
            properties=PublishProperties(
                correlation_data=msg.properties.correlation_data,
            ),
        )
```

### `PublishProperties` fields

| Field | Type | Description |
|-------|------|-------------|
| `payload_format_indicator` | `int \| None` | 0 = bytes, 1 = UTF-8 string |
| `message_expiry_interval` | `int \| None` | Seconds until broker discards |
| `topic_alias` | `int \| None` | Topic alias integer |
| `response_topic` | `str \| None` | Topic for response messages |
| `correlation_data` | `bytes \| None` | Request/response correlation token |
| `subscription_identifier` | `int \| None` | Set by broker, not by publisher |
| `content_type` | `str \| None` | MIME type of the payload |
| `user_properties` | `tuple[tuple[str, str], ...]` | Arbitrary key-value pairs |

## Subscribe options (5.0 only)

Additional keyword arguments are available on 5.0 connections:

```python
async with client.subscribe(
    "local/events",
    no_local=True,          # do not receive own publishes
    retain_as_published=True,  # preserve the retain flag as published
) as sub:
    ...
```

| Option | Type | Description |
|--------|------|-------------|
| `no_local` | `bool` | Skip messages published by this client |
| `retain_as_published` | `bool` | Forward the original retain flag, not the delivery flag |

!!! warning
    `no_local` and `retain_as_published` raise `RuntimeError` when used on a `version="3.1.1"` connection.

## Enhanced authentication (`client.auth()`)

Send an AUTH packet for extended authentication flows (e.g. SCRAM, Kerberos):

```python
await client.auth("SCRAM-SHA-256", data=b"client-first-message")
```

The `method` string is sent as the MQTT 5.0 `authentication_method` in the AUTH packet. This is an advanced feature — most applications do not need it.

## CONNACK / DISCONNECT reason codes

In MQTT 5.0, CONNACK and DISCONNECT packets carry a reason code. The library surfaces `MQTTConnectError` with `return_code` set to the CONNACK reason code on failure. Common 5.0 reason codes:

| Code | Name |
|------|------|
| 0x00 | Success |
| 0x04 | Disconnect with will message |
| 0x80 | Unspecified error |
| 0x81 | Malformed packet |
| 0x87 | Not authorised |
| 0x88 | Server unavailable |
| 0x8A | Bad authentication |

See the [MQTT 5.0 spec](https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html) for the full list.

---

**See also:** [Connecting — Version selection](../connecting.md#version-selection) · [Publishing](../publishing.md) · [Subscribing](../subscribing.md) · [Error Handling](../error-handling.md)
