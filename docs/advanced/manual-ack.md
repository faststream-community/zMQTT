# Manual Acknowledgement

By default, zmqtt acknowledges incoming messages automatically as soon as they are delivered to your queue. With `auto_ack=False` you take control: the broker-level ack is withheld until you explicitly call `msg.ack()`.

## Why use manual ack

Use `auto_ack=False` when acknowledgement must happen **after processing**. The broker does not receive the acknowledgement until your handler calls `ack()`.

Manual acknowledgement controls this ordering, but it does not make the handler transactional. Redelivery after a lost connection or process restart also depends on the broker session configuration.

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

For QoS 1 messages (`AT_LEAST_ONCE`), `ack()` sends PUBACK to the broker. Until PUBACK is sent, the broker may retransmit the message. Each retransmission appears as a new `Message` in your queue, so handlers should be idempotent.

## QoS 2 semantics

For QoS 2 messages (`EXACTLY_ONCE`), `ack()` sends PUBREC. The library then handles PUBREL and sends PUBCOMP automatically.

### QoS 2 deduplication during the manual-ack window

Between receiving the initial PUBLISH and calling `ack()`, PUBREC has not been sent, so the broker may retransmit the PUBLISH. Retransmitted PUBLISH packets for that delivery are ignored while the same connection remains active and the message is still unacknowledged.

## Connection loss before `ack()`

Manual acknowledgement alone does not preserve an unacknowledged delivery across reconnects. Broker redelivery requires a stable `client_id` and a persistent session:

**MQTT 3.1.1:**

```python
client = create_client(
    "localhost",
    client_id="jobs-worker-1",
    clean_session=False,
)
```

**MQTT 5.0:**

```python
client = create_client(
    "localhost",
    version="5.0",
    client_id="jobs-worker-1",
    clean_session=False,
    session_expiry_interval=3600,
)
```

With a persistent broker session, an unacknowledged QoS 1 or QoS 2 message may be delivered again after reconnect. QoS 2 duplicate suppression does not extend across reconnects or process restarts, so do not rely on it for exactly-once execution of application code.

The default connection settings start a clean session, so they do not provide durable redelivery after a connection or process is lost.

## Example: persistent job consumer

```python
import asyncio
from zmqtt import create_client, QoS

async def main():
    async with create_client(
        "localhost",
        client_id="jobs-worker-1",
        clean_session=False,
    ) as client:
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
                    # Leave the delivery unacknowledged. A persistent broker
                    # session makes it eligible for redelivery.
                    raise

asyncio.run(main())
```

---

**See also:** [Publishing — QoS levels](../publishing.md#qos-levels) · [Reconnection](reconnection.md) · [Error Handling](../error-handling.md)
