# Request / Response

MQTT 5.0 defines a first-class request/response pattern via two
`PUBLISH` properties: `response_topic` and `correlation_data`. zmqtt
implements this as a single `await client.request(…)` call.

## Basic usage

```python
from zmqtt import create_client

async with create_client("broker", version="5.0") as client:
    reply = await client.request("services/calculator", b"2+2")
    print(reply.payload)
```

`request()` handles the full flow automatically:

1. Subscribes to a unique reply topic before publishing.
2. Publishes the request with `response_topic` and `correlation_data` set.
3. Waits for the first message on the reply topic and returns it.
4. Unsubscribes on return, timeout, or cancellation.

## Customising via `PublishProperties`

Pass a `PublishProperties` instance to control any field of the outgoing
PUBLISH. Two fields receive special treatment:

| Field              | Behaviour                                                                              |
| ------------------ | -------------------------------------------------------------------------------------- |
| `response_topic`   | Used as the reply topic instead of the auto-generated one. Must not contain wildcards. |
| `correlation_data` | Forwarded to the responder as-is. Auto-generated (16 random bytes) when absent.        |

All other fields (`content_type`, `message_expiry_interval`,
`user_properties`, …) are forwarded unchanged.

```python
from zmqtt import PublishProperties

reply = await client.request(
    "services/translate",
    b"hello",
    properties=PublishProperties(
        content_type="text/plain",
        response_topic="my-app/replies/translate",
        correlation_data=b"req-001",
    ),
    timeout=10.0,
)
```

## Implementing a responder

The responder reads `response_topic` and `correlation_data` from the
incoming message and publishes the reply there:

```python
async with client.subscribe("services/translate") as sub:
    async for msg in sub:
        assert msg.properties is not None
        result = translate(msg.payload)
        await client.publish(
            msg.properties.response_topic,
            result,
            properties=PublishProperties(
                correlation_data=msg.properties.correlation_data,
            ),
        )
```

## Timeout

`request()` raises `asyncio.TimeoutError` when no reply arrives within
`timeout` seconds (default `30.0`). The reply subscription is always
cleaned up, even on timeout or cancellation.

```python
import asyncio

try:
    reply = await client.request("slow/service", b"ping", timeout=5.0)
except asyncio.TimeoutError:
    print("Service did not respond in time")
```

## Errors

| Exception               | Raised when                                       |
| ----------------------- | ------------------------------------------------- |
| `RuntimeError`          | `request()` is called on an MQTT 3.1.1 connection |
| `MQTTInvalidTopicError` | `properties.response_topic` contains wildcards    |
| `MQTTDisconnectedError` | Connection is lost while waiting for the reply    |
| `asyncio.TimeoutError`  | No reply arrives within `timeout` seconds         |

!!! note
`request()` is only available on MQTT 5.0 connections. Use
`create_client(…, version="5.0")` or `MQTTClient(…, version="5.0")`.

---

**See also:** [MQTT 5.0](mqtt5.md) · [Publishing](../publishing.md) · [Error Handling](../error-handling.md)
