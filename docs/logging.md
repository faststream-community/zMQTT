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
WARNING zmqtt.client  Connection to 10.0.0.5:1883 (client_id 'sensor-1') lost, reconnecting...
WARNING zmqtt.client  Connection to 10.0.0.5:1883 (client_id 'sensor-1') failed, retrying in 1.0s: ConnectionRefusedError: [Errno 111] Connect call failed ('10.0.0.5', 1883)
INFO    zmqtt.client  Successfully reconnected to 10.0.0.5:1883 (client_id 'sensor-1')
```

These lines name the broker as `host:port`, followed by the client ID when one is set.

Each failed attempt that will be retried is one line with the exception type and message. The traceback is added only when `zmqtt.client` is enabled for `DEBUG`.

Duplicate-filter warnings come from `zmqtt.protocol` (see [Subscribing — Duplicate-filter guard](subscribing.md#duplicate-filter-guard)):

```
WARNING zmqtt.protocol  Filter 'data/temp' already subscribed (ignored)
```

## Telling clients apart

Every record from `zmqtt.client` and `zmqtt.protocol` carries two extra attributes that say which client logged it:

| Attribute | Value |
|---|---|
| `mqtt_broker` | The broker as `host:port` (`[host]:port` for an IPv6 address) |
| `mqtt_client_id` | The `client_id` passed to the client; `""` if none was set |

The broker address is always there. The client ID is often empty: it defaults to `""`, and on MQTT 3.1.1 the client never learns the ID the broker assigns.

Use them in a format string, or read them in a filter or a structured-logging formatter:

```python
import logging

handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(levelname)s %(name)s [%(mqtt_broker)s] %(message)s"))
logging.getLogger("zmqtt").addHandler(handler)
```

A format string that names these attributes fails on records that lack them, so put it on a handler that only receives `zmqtt` records.

---

**See also:** [Reconnection](advanced/reconnection.md) · [Error Handling](error-handling.md)
