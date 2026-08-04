# Logging

## Logger hierarchy

zmqtt uses the standard `logging` module with the following logger names:

```
zmqtt
  ├── zmqtt.client
  └── zmqtt.protocol
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

## What DEBUG output looks like

At `DEBUG` level, the protocol logger reports connection and packet activity. A typical stream contains:

```
DEBUG zmqtt.protocol  Connecting
DEBUG zmqtt.protocol  Sending 27 bytes
INFO  zmqtt.protocol  Connected
DEBUG zmqtt.protocol  Sent SUBSCRIBE
DEBUG zmqtt.protocol  Received PingResp()
```

At `INFO` level the client layer logs reconnection events:

```
WARNING zmqtt.client  Connection lost, reconnecting...
INFO    zmqtt.client  Successfully reconnected
```

Duplicate-filter warnings come from `zmqtt.protocol` (see [Subscribing — Duplicate-filter guard](subscribing.md#duplicate-filter-guard)):

```
WARNING zmqtt.protocol  Filter 'data/temp' already subscribed (ignored)
```

---

**See also:** [Reconnection](advanced/reconnection.md) · [Error Handling](error-handling.md)
