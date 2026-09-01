<div align="center">

# zmqtt

[![PyPI version](https://shieldcn.dev/badge/dynamic/json.svg?url=https%3A%2F%2Fpypi.org%2Fpypi%2Fzmqtt%2Fjson&query=%24.info.version&variant=branded&size=xs&mode=light&logo=python&label=pypi+version)](https://pypi.org/project/zmqtt) 
[![PyPI downloads](https://shieldcn.dev/pypi/dm/zmqtt.svg?variant=branded&size=xs&logo=python&logoColor=ffffff)](https://pypistats.org/packages/zmqtt) 
[![PyPI requires python](https://shieldcn.dev/badge/dynamic/json.svg?url=https%3A%2F%2Fpypi.org%2Fpypi%2Fzmqtt%2Fjson&query=%24.info.requires_python&size=xs&mode=light&logo=python&logoColor=ffffff&label=requires+python&color=3775A9)](https://pypi.org/project/zmqtt)

<a href="https://github.com/faststream-community/zMQTT/actions/workflows/tests.yaml"><picture><source media="(prefers-color-scheme: dark)" srcset="https://shieldcn.dev/github/ci/faststream-community/zMQTT.svg?workflow=tests.yaml&branch=master&variant=outline&font=geist&size=xs&animate=pulse&mode=dark&label=Tests"><img alt="Tests" src="https://shieldcn.dev/github/ci/faststream-community/zMQTT.svg?workflow=tests.yaml&branch=master&variant=outline&font=geist&size=xs&animate=pulse&mode=light&label=Tests"></picture></a>
<a href="https://github.com/faststream-community/zMQTT/blob/master/LICENSE"><picture><source media="(prefers-color-scheme: dark)" srcset="https://shieldcn.dev/github/faststream-community/zMQTT/license.svg?variant=outline&font=geist&size=xs&mode=dark"><img alt="License" src="https://shieldcn.dev/github/faststream-community/zMQTT/license.svg?variant=outline&font=geist&size=xs&mode=light"></picture></a>
<a href="https://t.me/rumqtt"><picture><source media="(prefers-color-scheme: dark)" srcset="https://shieldcn.dev/badge/dynamic/json.svg?url=https%3A%2F%2Ftg.chirizxc.workers.dev%2Frumqtt&query=%24.members&suffix=+members&variant=outline&font=geist&size=xs&mode=dark&logo=telegram&logoColor=24A1DE&label=t.me/rumqtt"><img alt="Telegram members" src="https://shieldcn.dev/badge/dynamic/json.svg?url=https%3A%2F%2Ftg.chirizxc.workers.dev%2Frumqtt&query=%24.members&suffix=+members&variant=outline&font=geist&size=xs&mode=light&logo=telegram&logoColor=24A1DE&label=t.me/rumqtt"></picture></a>
<a href="https://t.me/python_faststream"><picture><source media="(prefers-color-scheme: dark)" srcset="https://shieldcn.dev/badge/dynamic/json.svg?url=https%3A%2F%2Ftg.chirizxc.workers.dev%2Fpython_faststream&query=%24.members&suffix=+members&variant=outline&font=geist&size=xs&mode=dark&logo=telegram&logoColor=24A1DE&label=t.me/python_faststream"><img alt="Telegram members" src="https://shieldcn.dev/badge/dynamic/json.svg?url=https%3A%2F%2Ftg.chirizxc.workers.dev%2Fpython_faststream&query=%24.members&suffix=+members&variant=outline&font=geist&size=xs&mode=light&logo=telegram&logoColor=24A1DE&label=t.me/python_faststream"></picture></a>
[![Coverage](https://coverage-badge.samuelcolvin.workers.dev/faststream-community/zMQTT.svg)](https://coverage-badge.samuelcolvin.workers.dev/redirect/faststream-community/zMQTT)

[Docs](https://faststream-community.github.io/zMQTT) ·
[PyPI](https://pypi.org/project/zmqtt) ·
[Changelog](https://github.com/faststream-community/zMQTT/blob/master/CHANGELOG.md) ·
[Contributing](https://github.com/faststream-community/zMQTT/blob/master/CHANGELOG.md) 

</div>

Pure asyncio MQTT 3.1.1 and 5.0 for Python 3.10+, with deterministic
subscription routing, bounded queues, and correlation-safe request/response.

## What is MQTT?

MQTT (Message Queuing Telemetry Transport) is a lightweight publish/subscribe
messaging protocol designed for devices with limited resources and networks
with low bandwidth or unstable connectivity. It became the de facto standard
in the IoT world thanks to its minimal packet overhead: a fixed header takes
as little as 2 bytes, which makes it cheap to send data even from small
embedded devices over slow or unreliable networks.

Official site: [mqtt.org](https://mqtt.org/)

## Why zmqtt?

- **Application-facing subscriptions.** Each `Subscription` owns its filters,
  bounded queue, acknowledgement policy, and async iterator.
- **Deterministic routing.** Wildcards, shared subscriptions, broker decorator
  prefixes, and MQTT 5 subscription identifiers are handled by the built-in
  router. One incoming PUBLISH is delivered once to the selected subscription.
- **High-level QoS.** Packet identifiers and the QoS 1/2 protocol handshakes are
  managed by the client. Manual acknowledgement is opt-in per subscription.
- **Safe MQTT 5 request/response.** Concurrent requests are matched by both
  response topic and correlation data and delivered exclusively to one pending
  request.
- **Bounded by default.** Subscription queues and pending request futures apply
  backpressure instead of growing without limit.
- **Self-contained MQTT stack.** The packet codec and protocol engine live in
  zmqtt; there is no Paho or other MQTT client dependency.
- **One API for MQTT 3.1.1 and 5.0.** `create_client()` returns a version-typed
  client, so a type checker can reject MQTT 5-only calls on a 3.1.1 connection.

## zmqtt and aiomqtt 3

[aiomqtt 3](https://github.com/empicano/aiomqtt) is no longer a Paho wrapper.
It is also pure asyncio, fully typed, and has MQTT 5 flow control and opt-in
automatic reconnection, but its API deliberately exposes more low-level MQTT 5
mechanics. zmqtt provides a higher-level application API and supports both
current protocol versions.

| | zmqtt | aiomqtt 3 |
|---|---|---|
| MQTT versions | 3.1.1 and 5.0 | 5.0 only |
| Receive model | Subscription-local queues and iterators | Client-wide `messages()` stream |
| Message routing | Built in: wildcards, shared/decorated filters, subscription identifiers | Left to the application |
| QoS 1/2 publish | Publish packet IDs and the complete handshake are managed by `publish()` | Caller supplies publish packet IDs and completes QoS 2 with `pubrel()` |
| Reconnection | Enabled by default; active `Subscription` objects are restored | Opt-in; no automatic resubscription—requires a persistent broker session or application-managed recovery |
| Request/response | `request()` with correlation routing, cleanup, and backpressure | MQTT properties exposed; correlation and response routing left to the application |
| MQTT protocol dependency | Built-in codec and state machines | External `mqtt5` package |

The comparison targets the
[aiomqtt 3.0 alpha API](https://github.com/empicano/aiomqtt/blob/main/docs/migration-v3.md),
where application code supplies packet identifiers for QoS 1/2 publishes,
drives the remaining QoS 2 steps, and implements message routing and response
correlation. zmqtt keeps the decisions applications need explicit — QoS,
acknowledgement timing, MQTT 5 properties, subscription lifecycle, and reconnect
policy — while handling that protocol machinery in the client. The result is
less application code, fewer protocol edge cases, and one typed API for both
MQTT 3.1.1 and 5.0.

## Installation

```bash
pip install zmqtt
```

## Quick start

```python
import asyncio

from zmqtt import QoS, create_client


async def main() -> None:
    async with create_client("localhost") as client:
        async with client.subscribe(
            "sensors/#",
            qos=QoS.AT_LEAST_ONCE,
        ) as messages:
            await client.publish(
                "sensors/temperature",
                "23.5",
                qos=QoS.AT_LEAST_ONCE,
            )
            msg = await messages.get_message()
            print(msg.topic, msg.payload.decode())


asyncio.run(main())
```

`create_client()` defaults to MQTT 3.1.1. Pass `version="5.0"` for the MQTT 5
API.

## Publish

```python
from zmqtt import QoS

await client.publish("events/online", b"device-42")
await client.publish(
    "commands/restart",
    b"device-42",
    qos=QoS.AT_LEAST_ONCE,
    retain=False,
)
```

Payloads may be `bytes` or `str`; strings are encoded as UTF-8. QoS 1 waits for
PUBACK. QoS 2 completes the PUBREC/PUBREL/PUBCOMP handshake before returning.

## Subscribe and route

```python
async with client.subscribe(
    "sensors/+/temperature",
    "sensors/#",
    receive_buffer_size=100,
) as messages:
    async for msg in messages:
        print(msg.topic, msg.payload)
```

If one PUBLISH matches multiple filters in the same subscription, zmqtt selects
the most specific filter (`literal` before `+` before `#`) and enqueues the
message once. MQTT 5 subscription identifiers are used when the broker supplies
them, which disambiguates overlapping subscriptions reliably.

Shared subscriptions and broker decorator prefixes work with the same API:

```python
async with client.subscribe("$share/workers/jobs/#") as jobs:
    async for job in jobs:
        await process(job)
```

The public `topic_matches()` helper follows the same matching rules, including
`$share`, `$queue`, `$exclusive`, and configured stripped prefixes.

## Manual acknowledgement

Manual acknowledgement only has an effect at QoS 1 or 2:

```python
from zmqtt import QoS, create_client

client = create_client(
    "localhost",
    client_id="orders-worker-1",
    clean_session=False,
)

async with client:
    async with client.subscribe(
        "orders/#",
        qos=QoS.AT_LEAST_ONCE,
        auto_ack=False,
    ) as messages:
        async for msg in messages:
            await save_to_database(msg)
            await msg.ack()
```

A stable client ID and persistent broker session make an unacknowledged message
eligible for redelivery after reconnect. They do not make application processing
exactly once, so handlers should still be idempotent. MQTT 5 clients also need a
positive `session_expiry_interval` for a session to survive disconnection.

## MQTT 5 request/response

```python
from zmqtt import create_client

async with create_client("localhost", version="5.0") as client:
    reply = await client.request(
        "services/echo",
        b"hello",
        timeout=5.0,
    )
    print(reply.payload)
```

zmqtt subscribes to the response topic before publishing, generates a response
topic and correlation data when omitted, and matches the reply by the exact
`(response_topic, correlation_data)` pair. Concurrent requests may share a
response topic. A matching reply is delivered only to its pending `request()`;
it is not also enqueued for an ordinary subscription. Unmatched or late replies
fall through to normal routing, which selects at most one subscription. The
request dispatcher itself does not retain them.

The responder must copy the request's correlation data unchanged:

```python
from zmqtt import PublishProperties

async with client.subscribe("services/echo") as requests:
    async for request in requests:
        assert request.properties is not None
        assert request.properties.response_topic is not None
        assert request.properties.correlation_data is not None
        await client.publish(
            request.properties.response_topic,
            request.payload,
            properties=PublishProperties(
                correlation_data=request.properties.correlation_data,
            ),
        )
```

The `timeout` argument bounds the reply wait after the response subscription is
ready and the request has been published. Use `asyncio.wait_for()` around the
whole coroutine when setup and publishing must be included in the cancellation
budget; cancellation cleanup can still extend the wall-clock completion time.

## Reconnection

Automatic reconnection is enabled by default. Active subscriptions are
re-registered after a successful reconnect while their application queues stay
alive. The default policy makes at most five connection attempts:

```python
from zmqtt import ReconnectConfig, create_client

client = create_client(
    "localhost",
    reconnect=ReconnectConfig(max_attempts=None),  # retry indefinitely
)
```

Broker refusals such as invalid credentials are not retried. See
[Reconnection](https://faststream-community.github.io/zMQTT/advanced/reconnection/)
for the complete failure semantics.

## MQTT 5 properties

```python
from zmqtt import PublishProperties, create_client

async with create_client("localhost", version="5.0") as client:
    await client.publish(
        "events/reading",
        b'{"value": 42}',
        properties=PublishProperties(
            content_type="application/json",
            message_expiry_interval=300,
            user_properties=(("source", "sensor-01"),),
        ),
    )
```

MQTT 5 also adds session expiry, subscription identifiers, `no_local`, retain
handling, publish properties, and a low-level AUTH packet API.

## Learn more

- [Getting started](https://faststream-community.github.io/zMQTT/getting-started/)
- [Connecting and TLS](https://faststream-community.github.io/zMQTT/connecting/)
- [Subscription routing](https://faststream-community.github.io/zMQTT/subscribing/)
- [Request / response](https://faststream-community.github.io/zMQTT/advanced/request-response/)
- [API reference](https://faststream-community.github.io/zMQTT/api-reference/)
