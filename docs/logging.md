# Logging

## Logger hierarchy

zmqtt uses the standard `logging` module with the following logger names:

```
zmqtt
  ├── zmqtt.transport
  ├── zmqtt.protocol
  └── zmqtt.client
```

Configure any of these with the standard `logging` API.

## Enabling logging

The simplest way to see all library output:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

For production, configure only the loggers you care about:

```python
import logging
logging.getLogger("zmqtt.client").setLevel(logging.WARNING)
logging.getLogger("zmqtt.protocol").setLevel(logging.DEBUG)
```

## No-handlers policy

zmqtt never installs handlers and never calls `logging.basicConfig()`. Your application controls all formatting, destinations, and log levels. The library only emits records — what happens to them is entirely up to you.

## What DEBUG output looks like

At `DEBUG` level, the protocol layer logs every packet sent and received:

```
DEBUG zmqtt.protocol  → CONNECT client_id='' clean_session=True keepalive=60
DEBUG zmqtt.protocol  ← CONNACK session_present=False return_code=0
DEBUG zmqtt.protocol  → SUBSCRIBE filters=['sensors/#']
DEBUG zmqtt.protocol  ← SUBACK return_codes=[0]
DEBUG zmqtt.protocol  ← PUBLISH topic='sensors/temp' qos=0 retain=False
DEBUG zmqtt.protocol  → PINGREQ
DEBUG zmqtt.protocol  ← PINGRESP
DEBUG zmqtt.protocol  → DISCONNECT
```

At `INFO` level the client layer logs reconnection events:

```
WARNING zmqtt.client  Connection lost, reconnecting in 1.0s
INFO    zmqtt.client  Successfully reconnected
```

Duplicate-filter warnings come from `zmqtt.protocol` (see [Subscribing — Duplicate-filter guard](subscribing.md#duplicate-filter-guard)):

```
WARNING zmqtt.protocol  Filter 'data/temp' already registered; skipping duplicate
```

---

**See also:** [Reconnection](advanced/reconnection.md) · [Error Handling](error-handling.md)
