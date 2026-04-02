# zmqtt

Pure asyncio MQTT 3.1.1 / 5.0 client.

## Install

```bash
pip install zmqtt
```

## Quick example

```python
import asyncio
from zmqtt import create_client, QoS

async def main():
    async with create_client("localhost") as client:
        async with client.subscribe("sensors/#", qos=QoS.AT_LEAST_ONCE) as sub:
            await client.publish("sensors/temp", "23.4")
            msg = await sub.get_message()
            print(msg.topic, msg.payload.decode())

asyncio.run(main())
```

## Links

- [Getting Started](getting-started.md)
- [API Reference](api-reference.md)
- [GitHub](https://github.com/toxicthunder/zmqtt)
