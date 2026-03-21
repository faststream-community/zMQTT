# Subscribing

## `client.subscribe()`

`subscribe()` returns a `Subscription` object. Use it as an async context manager — the SUBSCRIBE packet is sent to the broker on entry and UNSUBSCRIBE is sent on exit:

```python
async with client.subscribe("sensors/#") as sub:
    async for msg in sub:
        print(msg.topic, msg.payload.decode())
```

### Buffering and backpressure

Each `Subscription` buffers incoming messages in an internal queue. The size of this buffer is controlled by the `receive_buffer_size` parameter:

If `receive_buffer_size` > 0 - limits the queue to that number of messages
If `receive_buffer_size` == 0 - (default) → a safe default of 1000 messages is used

When the buffer is full, message delivery is naturally slowed down (backpressure) instead of growing memory usage indefinitely.

```python
async with client.subscribe("sensors/#", receive_buffer_size=100) as sub:
    async for msg in sub:
        process(msg)
```


## Async iterator

`async for` loops indefinitely. Break out when you are done:

```python
async with client.subscribe("commands/+") as sub:
    async for msg in sub:
        handle(msg)
        if msg.topic.endswith("/stop"):
            break
```

## Explicit pull with `get_message()`

`get_message()` returns the next message, suspending until one arrives:

```python
async with client.subscribe("telemetry/device/01") as sub:
    first  = await sub.get_message()
    second = await sub.get_message()
```

Use `get_message()` when you want pull-based control over when to read the next message. To bound memory when the consumer is slow, see [Backpressure](advanced/backpressure.md).

## `Message` fields

| Field | Type | Description |
|-------|------|-------------|
| `topic` | `str` | Full topic the message was published on |
| `payload` | `bytes` | Raw payload bytes |
| `qos` | `QoS` | QoS level of the incoming message |
| `retain` | `bool` | `True` if this is a retained message |
| `properties` | `PublishProperties \| None` | MQTT 5.0 properties; `None` on 3.1.1 |

## Multiple topic filters

Pass multiple filters in a single call. One `Subscription` object covers all of them:

```python
async with client.subscribe("sensors/#", "alerts/+", "status") as sub:
    async for msg in sub:
        ...
```

## QoS on subscribe

```python
async with client.subscribe("data/#", qos=QoS.AT_LEAST_ONCE) as sub:
    ...
```

The broker delivers messages at the lower of the publish QoS and the subscribe QoS.

## `RetainHandling` (MQTT 5.0)

`RetainHandling` controls whether retained messages are sent when you subscribe. Available values:

| Value | Behaviour |
|-------|-----------|
| `SEND_ON_SUBSCRIBE` (default) | Send retained messages on every SUBSCRIBE |
| `SEND_IF_NOT_EXISTS` | Send only if no subscription already exists |
| `DO_NOT_SEND` | Never send retained messages |

```python
from zmqtt import RetainHandling

async with client.subscribe("status/#", retain_handling=RetainHandling.SEND_IF_NOT_EXISTS) as sub:
    ...
```

## Wildcard filter priority

When multiple filters in the same `Subscription` all match an incoming topic, only the most specific filter's queue receives the message — delivery is never duplicated.

Specificity is compared level-by-level, left to right:

- Literal segment beats `+`
- `+` beats `#`

**Example:** if you subscribe to both `a/b` and `a/#`, a message on `a/b` is routed to `a/b` only:

```python
async with client.subscribe("a/b", "a/#") as sub:
    # message published to "a/b" → delivered once, matched by "a/b"
    # message published to "a/c" → delivered once, matched by "a/#"
```

**Important notes**
1. If you have multiple matched subscription, different brokers have different behaviour with it. *HiveMQ* sends
only one message, *Artemis*, *Mosquitto* and *NanoMQ* send same message for each match.
2. When two filters tie (same specificity), the library logs a `WARNING` and delivers to whichever filter was registered first.

## Duplicate-filter guard

!!! warning
    Subscribing the same filter string across two separate `Subscription` objects logs a `WARNING`. The second subscription gets no queue for that filter — it will receive `get_message()` results from other filters only. The SUBSCRIBE is still forwarded to the broker.

```python
async with client.subscribe("data/temp") as sub1:
    async with client.subscribe("data/temp") as sub2:  # WARNING logged
        # sub2 receives nothing for "data/temp"
        ...
```

Prefer multiple filters in a single `subscribe()` call rather than multiple overlapping subscriptions. See [Logging](logging.md) for how to observe this warning at runtime.

---

**See also:** [Manual Ack](advanced/manual-ack.md) · [Backpressure](advanced/backpressure.md) · [MQTT 5.0](advanced/mqtt5.md)
