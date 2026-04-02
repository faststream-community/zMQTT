# Scaling with Shared Subscriptions

## The problem: fan-out vs load balancing

By default MQTT uses **fan-out**: every subscriber to a topic receives every message. If you run two workers subscribed to `jobs/#`, each message lands in both workers — work is duplicated.

**Shared subscriptions** solve this. Clients join a named group, and the broker distributes each message to exactly one group member — standard round-robin load balancing with no application-level coordination.

## Syntax

Subscribe to `$share/<group>/<topic>` instead of `<topic>` directly. The group name is arbitrary; all workers that should share load must use the same group name. The publisher publishes to the plain topic as usual.

```python
import asyncio
from zmqtt import MQTTClient, QoS


async def worker(worker_id: int) -> None:
    async with MQTTClient("broker.example.com") as client:
        async with client.subscribe("$share/workers/jobs/#", qos=QoS.AT_LEAST_ONCE) as sub:
            async for msg in sub:
                print(f"worker {worker_id} got {msg.topic}: {msg.payload}")


async def main() -> None:
    # Both workers receive disjoint subsets of messages — no duplicates
    await asyncio.gather(worker(1), worker(2))
```

The publisher needs no changes:

```python
async with MQTTClient("broker.example.com") as client:
    await client.publish("jobs/resize", b"image-42.jpg", qos=QoS.AT_LEAST_ONCE)
```

## QoS recommendation

Use **QoS 1** (`AT_LEAST_ONCE`) or **QoS 2** (`EXACTLY_ONCE`) for shared subscriptions. With QoS 0 the broker makes no delivery guarantees; if the chosen worker disconnects mid-flight the message is silently dropped. QoS 1 ensures the message is redelivered to another group member on reconnect.

For strict exactly-once semantics use QoS 2 or handle idempotency at the application layer with QoS 1 and [manual ack](manual-ack.md).

## Broker compatibility

All brokers supported by zmqtt's test suite accept the `$share/<group>/<topic>` syntax for both MQTT 3.1.1 and 5.0 connections:

| Broker | Supported since | Notes |
|--------|----------------|-------|
| Apache ActiveMQ Artemis | 2.16.0 | MQTT 3.1.1 and 5.0 |
| Eclipse Mosquitto | 2.0.0 | MQTT 3.1.1 and 5.0 |
| HiveMQ CE | 2021.1 | MQTT 3.1.1 and 5.0 |
| NanoMQ | 0.6.0 | MQTT 3.1.1 and 5.0; rejects double-slash filters — see note below |

> **NanoMQ — avoid double slashes in shared filters**
>
> NanoMQ strictly validates topic filters and rejects any filter containing `//` (two consecutive slashes). This can happen silently if your base topic starts with a leading slash:
>
> ```python
> topic = "/sensors/temp"               # leading slash
> shared = f"$share/workers/{topic}"    # → "$share/workers//sensors/temp"  ❌
> ```
>
> Strip the leading slash before building the shared filter:
>
> ```python
> topic = "/sensors/temp"
> shared = f"$share/workers/{topic.lstrip('/')}"  # → "$share/workers/sensors/temp"  ✓
> ```
>
> Other brokers tolerate the double slash, but NanoMQ disconnects the client immediately on SUBSCRIBE.

Shared subscriptions are part of the MQTT 5.0 specification. Support in MQTT 3.1.1 is a broker extension — all major brokers have shipped it, but check your broker's release notes if you use an older version.

---

**See also:** [Manual Ack](manual-ack.md) · [Backpressure](backpressure.md)
