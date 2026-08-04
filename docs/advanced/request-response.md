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

1. Acquires the response topic before publishing. When omitted, a unique topic
   is generated automatically.
2. Publishes the request with `response_topic` and `correlation_data` set.
3. Waits for a message with matching `correlation_data` and returns it.
4. Releases its response-topic interest on return, timeout, or cancellation.

Messages without correlation data or with a different value do not complete
the request. If a regular `Subscription` also covers the response topic, it
still receives every message: request routing observes deliveries without
consuming the subscription queue.

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

The same custom response topic can be shared by concurrent requests as long as
their correlation data differs. Responses may arrive in any order; each one is
routed to the matching `request()` call. Reusing the same response topic and
correlation data while a request is active raises `ValueError` because the two
responses would be indistinguishable.

## Implementing a responder

The responder reads `response_topic` and `correlation_data` from the
incoming message and publishes the reply there:

```python
async with client.subscribe("services/translate") as sub:
    async for msg in sub:
        assert msg.properties is not None
        assert msg.properties.response_topic is not None
        assert msg.properties.correlation_data is not None
        result = translate(msg.payload)
        await client.publish(
            msg.properties.response_topic,
            result,
            properties=PublishProperties(
                correlation_data=msg.properties.correlation_data,
            ),
        )
```

The [MQTT 5.0 request/response flow, section 4.10.1](https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html)
specifies that when a Request Message contains Correlation Data, the responder
copies that property into the Response Message. `request()` always includes
Correlation Data, using the caller-provided value or generating 16 random
bytes. A compatible responder therefore has to return the same bytes unchanged;
it must not omit the property or generate a new value.

The broker only forwards Correlation Data; it does not add it to the response
on behalf of the responder. A response without Correlation Data, or with a
different value, cannot be associated with the active request. `request()`
ignores that message and continues waiting for a matching response until its
timeout. Any regular subscription covering the response topic still receives
the message.

## Timeout

`request()` raises `asyncio.TimeoutError` when no matching reply arrives within
`timeout` seconds (default `30.0`). The client stops listening for the matching
response after return, timeout, or cancellation.

After a timeout, a late response is not retained in memory. It cannot complete
the expired request, but it is still delivered to any regular subscription
covering that topic.

```python
import asyncio

try:
    reply = await client.request("slow/service", b"ping", timeout=5.0)
except asyncio.TimeoutError:
    print("Service did not respond in time")
```

## Connection loss and reconnection

With automatic reconnection enabled (the default), a request that has already been published continues waiting for its matching response. Its response topic is restored after reconnect, and the original `timeout` continues to apply across the interruption.

With reconnection disabled, a request that is already waiting remains pending until its timeout expires. Calling `client.disconnect()` while requests are pending ends them with `MQTTDisconnectedError`.

## Errors

| Exception               | Raised when                                       |
| ----------------------- | ------------------------------------------------- |
| `RuntimeError`          | `request()` is called on an MQTT 3.1.1 connection |
| `MQTTInvalidTopicError` | `properties.response_topic` contains wildcards    |
| `MQTTDisconnectedError` | The request cannot start, or `client.disconnect()` is called while it is active |
| `ValueError`            | Topic/correlation pair is already in use          |
| `asyncio.TimeoutError`  | No matching reply arrives within `timeout`        |

## Request backpressure

The client keeps one pending result per active request and never buffers
unmatched or late response messages. `max_pending_requests` bounds the number
of active requests (default `1000`): additional calls wait for capacity before
publishing.

```python
client = create_client(
    "broker",
    version="5.0",
    max_pending_requests=100,
)
```

!!! note
`request()` is only available on MQTT 5.0 connections. Use
`create_client(…, version="5.0")` or `MQTTClient(…, version="5.0")`.

---

**See also:** [MQTT 5.0](mqtt5.md) · [Publishing](../publishing.md) · [Error Handling](../error-handling.md)
