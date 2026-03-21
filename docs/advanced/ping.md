# Manual Ping

## `client.ping()`

Send a PINGREQ and await the PINGRESP. Returns the round-trip time in seconds:

```python
rtt = await client.ping()
print(f"Broker RTT: {rtt * 1000:.1f} ms")
```

## Timeout parameter

```python
rtt = await client.ping(timeout=5.0)   # raises MQTTTimeoutError if no response in 5 s
```

Default timeout is 10 seconds. `MQTTTimeoutError` is raised if PINGRESP does not arrive within the timeout.

```python
from zmqtt.errors import MQTTTimeoutError

try:
    rtt = await client.ping(timeout=2.0)
except MQTTTimeoutError:
    print("Broker is not responding")
```

## Keepalive vs manual ping

The library automatically sends PINGREQ packets on the keepalive interval configured at connection time (see [`keepalive` parameter](../connecting.md#connection-parameters), default 60 s). This keeps the TCP connection alive and lets the broker detect dead clients.

`client.ping()` is for **application-level** use — measuring latency, confirming broker reachability before a critical operation, or implementing a health check. It is independent of the automatic keepalive ping loop.

## Concurrent pings

Multiple concurrent `ping()` calls are safe. Each waiter registers independently and receives its own RTT measurement when the next PINGRESP arrives.

---

**See also:** [Error Handling — MQTTTimeoutError](../error-handling.md#mqtttimeouterror) · [Connecting](../connecting.md)
