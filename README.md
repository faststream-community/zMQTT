# zmqtt

Pure asyncio MQTT 3.1.1 and 5.0 client library. No paho dependency, no threading, no god classes. See [documentation](https://faststream-community.github.io/zMQTT/).

## Why not aiomqtt?

[aiomqtt](https://github.com/sbtinstruments/aiomqtt) is a thin async wrapper around paho-mqtt.
You inherit paho's threading model, 10 000-line files, and implicit global state — just with `async/await` painted on top.

zmqtt is built from scratch:

|                  | zmqtt                                | aiomqtt (paho)                |
| ---------------- | ------------------------------------ | ----------------------------- |
| I/O model        | pure asyncio                         | paho threads + asyncio bridge |
| Packet codec     | pure functions, I/O-free             | paho internals                |
| MQTT 5.0         | native, typed properties dataclasses | partial                       |
| Type annotations | strict mypy                          | partial                       |
| Backpressure     | bounded subscription queues          | none                          |

## Installation

```bash
pip install zmqtt
```

## Quick start

```python
import asyncio
from zmqtt import MQTTClient

async def main():
    async with MQTTClient("broker.example.com") as client:
        async with client.subscribe("sensors/#") as messages:
            async for msg in messages:
                print(msg.topic, msg.payload)

asyncio.run(main())
```

Or manage connections and subscriptions manually:

```python
import asyncio
from zmqtt import MQTTClient

async def main():
    client = MQTTClient("broker.example.com")
    await client.connect()

    subscription = client.subscribe("sensors/#")
    await subscription.start()

    msg = await subscription.get_message()
    print(msg.topic, msg.payload)

    await subscription.stop()
    await client.disconnect()

asyncio.run(main())
```

## Publish

```python
async with MQTTClient("broker.example.com") as client:
    await client.publish("sensors/temperature", b"23.5", qos=1)
```

## QoS levels

```python
from zmqtt import QoS

await client.publish("topic", b"data", qos=QoS.AT_LEAST_ONCE)   # QoS 1
await client.publish("topic", b"data", qos=QoS.EXACTLY_ONCE)    # QoS 2
```

## Manual acknowledgement

Hold the PUBACK/PUBREC until your application has durably processed the message:

```python
async with client.subscribe("orders/#", auto_ack=False) as messages:
    async for msg in messages:
        await save_to_database(msg)
        await msg.ack()  # broker will redeliver if we crash before this
```

## Subscription as explicit get

Useful when interleaving message handling with other async work:

```python
async with client.subscribe("sensors/#") as messages:
    msg = await messages.get_message()
    print(msg.topic, msg.payload)
```

## Reconnection

`MQTTClient` reconnects automatically with exponential backoff. Active subscriptions
are transparently re-registered after reconnect — your `async for` loop keeps running.

## MQTT 5.0

Pass `version=5` to use MQTT 5.0. Properties are typed dataclasses:

```python
from zmqtt import MQTTClient
from zmqtt._internal.packets.properties import PublishProperties

async with MQTTClient("broker.example.com", version=5) as client:
    props = PublishProperties(content_type="application/json")
    await client.publish("topic", b'{"value": 42}', properties=props)
```
