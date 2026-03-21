# Publishing

## Basic usage

```python
await client.publish("sensors/temperature", "23.4")
```

`publish()` is a coroutine. For QoS 0 it returns as soon as the packet is written to the socket. For QoS 1 and 2 it awaits the broker's acknowledgement before returning.

## Payload types

The `payload` argument accepts `bytes` or `str`. Strings are encoded as UTF-8:

```python
await client.publish("topic", "hello")          # str → bytes("hello", "utf-8")
await client.publish("topic", b"\x00\x01\x02")  # raw bytes
```

## QoS levels

| QoS | Name | Guarantee |
|-----|------|-----------|
| `QoS.AT_MOST_ONCE` (0) | Fire-and-forget | Delivered at most once; no retries |
| `QoS.AT_LEAST_ONCE` (1) | Confirmed | Delivered at least once; broker sends PUBACK |
| `QoS.EXACTLY_ONCE` (2) | Exactly-once | Delivered exactly once; full PUBREC/PUBREL/PUBCOMP handshake |

```python
from zmqtt import QoS

await client.publish("cmd/device/restart", b"1", qos=QoS.AT_LEAST_ONCE)
```

`QoS.AT_MOST_ONCE` is the default.

## Retain flag

A retained message is stored by the broker and delivered to any future subscriber immediately on subscribe:

```python
await client.publish("status/service", "online", retain=True)
```

## MQTT 5.0 publish properties

When connected with `version="5.0"`, you can attach a `PublishProperties` object:

```python
from zmqtt import create_client
from zmqtt.packets.properties import PublishProperties

async with create_client("localhost", version="5.0") as client:
    props = PublishProperties(
        message_expiry_interval=300,   # seconds until broker discards
        content_type="application/json",
        response_topic="replies/app",
        correlation_data=b"req-123",
        user_properties=(("source", "sensor-01"),),
    )
    await client.publish("data/readings", b'{"temp": 23.4}', properties=props)
```

!!! warning
    Passing `properties` on a `version="3.1.1"` connection raises `RuntimeError`.

See [MQTT 5.0](advanced/mqtt5.md) for the full properties reference.

---

**See also:** [Subscribing](subscribing.md) · [Manual Ack](advanced/manual-ack.md) · [Error Handling](error-handling.md)
