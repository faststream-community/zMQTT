# Getting Started

## Prerequisites

Python 3.10 or later.

## Installation

```bash
pip install zmqtt
```

## Connecting to a broker

Use `create_client()` as an async context manager. The connection is established on entry and cleanly closed on exit.

```python
import asyncio
from zmqtt import create_client

async def main():
    async with create_client("localhost") as client:
        ...  # client is connected here

asyncio.run(main())
```

For TLS, pass `tls=True` to use the system CA bundle, or pass an `ssl.SSLContext` for custom certificates:

```python
async with create_client("broker.example.com", port=8883, tls=True) as client:
    ...
```

See [Connecting](connecting.md) for all connection options — client ID, keepalive, credentials, TLS, and reconnection.

## Publishing a message

```python
await client.publish("home/temperature", "23.4")
```

Payloads can be `bytes` or `str`. Strings are UTF-8 encoded automatically. See [Publishing](publishing.md) for QoS levels and the retain flag.

## Receiving messages

`client.subscribe()` returns a `Subscription` — use it as an async context manager, then iterate:

```python
async with client.subscribe("home/#") as sub:
    async for msg in sub:
        print(msg.topic, msg.payload.decode())
```

The subscription is automatically sent to the broker on entry and unsubscribed on exit. See [Subscribing](subscribing.md) for wildcard filters, explicit pull with `get_message()`, and manual acknowledgement.

## Full runnable example

```python
import asyncio
from zmqtt import create_client, QoS

async def main():
    async with create_client("localhost") as client:
        async with client.subscribe("home/#", qos=QoS.AT_LEAST_ONCE) as sub:
            await client.publish("home/temperature", "23.4")
            await client.publish("home/humidity", "55")

            for _ in range(2):
                msg = await sub.get_message()
                print(f"{msg.topic}: {msg.payload.decode()}")

asyncio.run(main())
```

Run a local broker first:

```bash
docker compose up -d artemis
```
