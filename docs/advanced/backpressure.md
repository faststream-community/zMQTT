# Backpressure

## `receive_buffer_size`

By default, the internal message queue for each `Subscription` is bounded to `1000`. Set `receive_buffer_size` to change it:

```python
async with client.subscribe("telemetry/#", receive_buffer_size=100) as sub:
    async for msg in sub:
        await slow_process(msg)
```

`receive_buffer_size` is passed directly to `asyncio.Queue(maxsize=...)`. When the queue is full, `put()` blocks.

## How flow control works

The library's read loop reads packets from the TCP stream and dispatches them. When a `Subscription` queue is full:

1. The relay task that moves messages from the internal protocol queue to your subscription queue blocks on `queue.put()`.
2. The protocol's internal queue for that filter fills up.
3. Read loop stops reading new data from the socket.
4. The TCP receive buffer fills.
5. The TCP stack signals backpressure to the broker via window size reduction.

The result is end-to-end flow control: a slow consumer naturally slows the broker's publish rate without any explicit coordination code.

## When to use it

Use `receive_buffer_size` when:

- Your message handler is slow (I/O-bound, database writes, etc.) and you want to bound memory usage.
- You need guaranteed processing of every message without unbounded queue growth.
- You are implementing a consumer that must apply backpressure to upstream producers.

Set it to `0` (unbounded) when:

- Message arrival rate is low or bounded.
- You buffer messages yourself (e.g. writing to a database in batches).
- You prefer to drop or log excess rather than slow the broker.

!!! warning
    Applying backpressure affects all topics multiplexed on the same TCP connection. A slow consumer on one `Subscription` will stall delivery to all other subscriptions on the same client. If you need independent flow control per topic, use separate clients.

---

**See also:** [Subscribing](../subscribing.md) · [Manual Ack](manual-ack.md)
