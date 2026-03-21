# Manual Acknowledgement

By default, zmqtt acknowledges incoming messages automatically as soon as they are delivered to your queue. With `auto_ack=False` you take control: the broker-level ack is withheld until you explicitly call `msg.ack()`.

## Why use manual ack

Use `auto_ack=False` when you need a **process-before-ack** guarantee: the message should not be considered delivered until your processing is complete. If your process crashes before calling `ack()`, the broker (for QoS 1) or the library (for QoS 2) will redeliver.

!!! note
    Manual ack only has effect at QoS 1 (`AT_LEAST_ONCE`) or QoS 2 (`EXACTLY_ONCE`). At QoS 0 (`AT_MOST_ONCE`) there is no acknowledgement protocol, so `auto_ack=False` is a no-op. See [QoS levels](../publishing.md#qos-levels) for the delivery guarantees.

## Enabling manual ack

```python
async with client.subscribe("jobs/#", qos=QoS.AT_LEAST_ONCE, auto_ack=False) as sub:
    async for msg in sub:
        await process(msg)   # do your work first
        await msg.ack()      # then acknowledge
```

`msg.ack()` is idempotent — calling it multiple times is safe, subsequent calls are no-ops.

## QoS 1 semantics

For QoS 1 messages (`AT_LEAST_ONCE`), `ack()` sends PUBACK to the broker. Until PUBACK is sent, the broker may retransmit the message (e.g. on reconnect). Each retransmission appears as a new `Message` in your queue.

## QoS 2 semantics

For QoS 2 messages (`EXACTLY_ONCE`), `ack()` sends PUBREC. The library then handles PUBREL and sends PUBCOMP automatically.

### QoS 2 deduplication during the manual-ack window

Between receiving the initial PUBLISH and calling `ack()`, PUBREC has not been sent, so the broker may retransmit the PUBLISH (e.g. after a reconnect). The library silently drops the duplicate — your application sees the message exactly once regardless of how many retransmissions occur.

## What happens on reconnect before `ack()`

- **QoS 1**: the broker retransmits the message after reconnection. Your queue receives it again as a new message with the DUP flag set.
- **QoS 2**: the library's deduplication logic ensures only one delivery regardless of reconnects.

## Example: durable job processing

```python
import asyncio
from zmqtt import create_client, QoS

async def main():
    async with create_client("localhost") as client:
        async with client.subscribe(
            "jobs/process",
            qos=QoS.AT_LEAST_ONCE,
            auto_ack=False,
        ) as sub:
            async for msg in sub:
                try:
                    await process_job(msg.payload)
                    await msg.ack()
                except Exception:
                    # Do NOT ack — broker will redeliver
                    raise

asyncio.run(main())
```

---

**See also:** [Publishing — QoS levels](../publishing.md#qos-levels) · [Reconnection](reconnection.md) · [Error Handling](../error-handling.md)
