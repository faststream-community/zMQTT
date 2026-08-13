# zmqtt

Pure asyncio MQTT 3.1.1 and 5.0 for Python 3.10+.

zmqtt provides application-facing subscriptions rather than one client-wide
message stream: each `Subscription` owns its filters, bounded queue,
acknowledgement policy, and lifecycle. The built-in router handles wildcard and
shared filters, MQTT 5 subscription identifiers, and correlation-safe
request/response.

## Install

```bash
pip install zmqtt
```

## Quick example

```python
import asyncio

from zmqtt import QoS, create_client


async def main() -> None:
    async with create_client("localhost") as client:
        async with client.subscribe(
            "sensors/#",
            qos=QoS.AT_LEAST_ONCE,
        ) as sub:
            await client.publish(
                "sensors/temp",
                "23.4",
                qos=QoS.AT_LEAST_ONCE,
            )
            msg = await sub.get_message()
            print(msg.topic, msg.payload.decode())


asyncio.run(main())
```

## Highlights

- MQTT 3.1.1 and 5.0 behind one version-typed factory.
- Subscription-local async iterators and bounded backpressure.
- Deterministic wildcard, shared-subscription, and identifier-based routing.
- Automatic packet IDs and complete QoS 1/2 publish handshakes.
- Optional manual acknowledgement per subscription.
- MQTT 5 `request()` with exact correlation matching and bounded concurrency.
- Automatic reconnect and re-subscription with a configurable retry policy.
- Built-in packet codec and protocol state machines; no Paho dependency.

## Start here

- [Getting Started](getting-started.md)
- [Connecting and TLS](connecting.md)
- [Publishing](publishing.md)
- [Subscribing and routing](subscribing.md)
- [MQTT 5 request / response](advanced/request-response.md)
- [API Reference](api-reference.md)
- [GitHub](https://github.com/faststream-community/zMQTT)
