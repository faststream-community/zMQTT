# Scaling with Shared Subscriptions

## The problem: fan-out vs load balancing

By default MQTT uses **fan-out**: every subscriber to a topic receives every message. If you run two workers subscribed to `jobs/#`, each message lands in both workers — work is duplicated.

**Shared subscriptions** solve this. Clients join a named group, and the broker
selects one group member for each message. The selection strategy is
broker-specific; MQTT does not require round-robin distribution.

## Syntax

Subscribe to `$share/<group>/<topic>` instead of `<topic>` directly. The group name is arbitrary; all workers that should share load must use the same group name. The publisher publishes to the plain topic as usual.

```python
import asyncio
from zmqtt import QoS, create_client


async def worker(worker_id: int) -> None:
    async with create_client("broker.example.com") as client:
        async with client.subscribe("$share/workers/jobs/#", qos=QoS.AT_LEAST_ONCE) as sub:
            async for msg in sub:
                print(f"worker {worker_id} got {msg.topic}: {msg.payload}")


async def main() -> None:
    # Both workers receive disjoint subsets of messages — no duplicates
    await asyncio.gather(worker(1), worker(2))
```

The publisher needs no changes:

```python
async with create_client("broker.example.com") as client:
    await client.publish("jobs/resize", b"image-42.jpg", qos=QoS.AT_LEAST_ONCE)
```

## Broker-specific subscription prefixes

Some brokers provide group-less subscription prefixes in addition to the standard `$share/<group>/...` syntax. zmqtt recognises `$queue/...` and `$exclusive/...` by default. The broker removes such a prefix before delivering the message, so a subscription to `$queue/jobs/#` receives messages published to `jobs/#`.

Use `stripped_prefixes` to support another prefix used by your broker. The tuple replaces the defaults, so include any default prefixes you still need:

```python
client = create_client(
    "broker.example.com",
    stripped_prefixes=("$queue", "$exclusive", "$q"),
)
```

The public `topic_matches()` helper follows the same rules when matching topics outside an active subscription:

```python
from zmqtt import topic_matches

assert topic_matches("$queue/jobs/+", "jobs/resize")
assert topic_matches("$q/jobs/+", "jobs/resize", stripped_prefixes=("$q",))
```

Do not add namespaces such as `$SYS` to `stripped_prefixes`: brokers publish those topics with the namespace intact.

## QoS recommendation

Use **QoS 1** (`AT_LEAST_ONCE`) or **QoS 2** (`EXACTLY_ONCE`) when losing a
message during worker failure is unacceptable. QoS 0 has no acknowledgement, so
a delivery interrupted by disconnection can be lost. QoS 1/2 makes the delivery
eligible for retry, but whether another group member receives it depends on the
broker and persistent shared-subscription session.

QoS 2 prevents duplicate protocol delivery within the MQTT session; it does not
make external application side effects exactly once. Keep handlers idempotent,
or combine QoS 1 with [manual acknowledgement](manual-ack.md) and an application
deduplication key.

## Broker compatibility

All brokers supported by zmqtt's test suite accept the `$share/<group>/<topic>` syntax for both MQTT 3.1.1 and 5.0 connections:

| Broker | MQTT versions tested | Notes |
|--------|----------------------|-------|
| Apache ActiveMQ Artemis | 3.1.1 and 5.0 | Standard `$share/<group>/...` syntax |
| Eclipse Mosquitto | 3.1.1 and 5.0 | Standard `$share/<group>/...` syntax |
| EMQX | 3.1.1 and 5.0 | Also tested with group-less `$queue/...` subscriptions |
| HiveMQ CE | 3.1.1 and 5.0 | Standard `$share/<group>/...` syntax |
| NanoMQ | 3.1.1 and 5.0 | Rejects double-slash filters — see note below |

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

Shared subscriptions are part of MQTT 5.0. On MQTT 3.1.1 they are a broker
extension; check the broker's documentation.

---

**See also:** [Manual Ack](manual-ack.md) · [Backpressure](backpressure.md)
