# Persistent sessions

A persistent MQTT session keeps broker-side subscriptions and queued messages
across network connections. It is useful for command consumers that must receive
QoS 1 or QoS 2 messages published while they are offline.

## Configuration

Use a stable, application-assigned `client_id`. For MQTT 3.1.1, disable clean
sessions:

```python
client = create_client(
    "localhost",
    client_id="commands-worker-1",
    clean_session=False,
)
```

For MQTT 5.0, also choose a positive session expiry interval:

```python
client = create_client(
    "localhost",
    version="5.0",
    client_id="commands-worker-1",
    clean_session=False,
    session_expiry_interval=3600,
)
```

The broker reports whether it resumed an existing session through the CONNACK
Session Present flag. If no session exists, subscriptions behave like ordinary
new subscriptions.

## Startup replay

A broker can send queued messages immediately after CONNACK, before application
code has entered its `client.subscribe(...)` contexts. When Session Present is
set, zmqtt temporarily holds an incoming message that has no local recipient.
The message remains unacknowledged at the protocol level.

Whenever a local subscription is declared or restored, zmqtt checks the held
messages again. Each matching message is moved to that `Subscription` object's
normal queue and continues through its configured acknowledgement policy:

- with `auto_ack=True`, zmqtt completes the QoS acknowledgement automatically;
- with `auto_ack=False`, the message remains unacknowledged until `msg.ack()`.

Replay never bypasses `receive_buffer_size`. If the subscription queue fills,
the remaining messages stay unacknowledged in the replay buffer. Reading from
the subscription makes room and resumes the transfer, so entering the
subscription does not wait for the whole offline backlog to fit in its queue.

The first unmatched message starts a replay grace period controlled by
`session_replay_timeout`, which defaults to 30 seconds. When it ends, zmqtt first
rechecks every held message and delivers those whose recipient has capacity. It
then writes one warning and drops anything still held without sending PUBACK or
PUBREC. Later incoming messages use ordinary routing for the rest of that
connection: they are delivered when a local recipient exists and otherwise
acknowledged and dropped.

```python
client = create_client(
    "localhost",
    client_id="commands-worker-1",
    clean_session=False,
    session_replay_timeout=60.0,
)
```

The timeout is measured from the first buffered message and is not extended by
later arrivals. It protects the process from retaining replay indefinitely when
application code never restores the corresponding subscription. Set it high
enough for the application's normal startup sequence and offline backlog.

Dropping is local only. QoS 1 and QoS 2 exchanges remain incomplete at the
broker, so their messages can be replayed again after the next resumed
connection. Until then, those unacknowledged messages may continue occupying
the broker's in-flight delivery capacity.

If the connection closes before the timeout, the local replay buffer is
discarded without acknowledgement. The broker remains responsible for replaying
its QoS 1 and QoS 2 messages on the next resumed connection.

## Subscription declaration order

Held messages are reconsidered after each subscription declaration. The first
declared subscription selected by normal routing receives the message. Exact
filters remain more specific than `+`, which remains more specific than `#`,
among all subscriptions active at that moment.

If several application subscriptions overlap and their declaration order
matters, declare them from most specific to least specific. MQTT 5 subscription
identifiers are used when the broker includes them, with the same fallback to
the currently active filters as ordinary live-message routing.

## Buffer limit

`session_replay_buffer_size` limits how many unmatched replay messages can be
held per connection. The default is `1000`; `0` makes it unbounded:

```python
client = create_client(
    "localhost",
    client_id="commands-worker-1",
    clean_session=False,
    session_replay_buffer_size=5000,
)
```

If the limit is exceeded before the replay timeout, zmqtt closes the transport
and raises `MQTTProtocolError` without acknowledging the overflowing message.
This is a fail-closed policy: QoS 1 and QoS 2 messages remain in the broker
session rather than being silently discarded. An unbounded buffer can still
exhaust process memory during the grace period under a sufficiently high
message rate.

QoS 0 has no durable redelivery guarantee. It can wait in the in-process replay
buffer once received, but a disconnect or process failure can still lose it.

## Processing guarantees

Persistent sessions and QoS protect transport delivery, not the side effects of
application handlers. With automatic acknowledgement, a process failure after
queueing but before completing business work can still lose that work. With
manual acknowledgement, a failure before `msg.ack()` causes redelivery and can
therefore execute a handler more than once.

Use idempotent handlers or application-level deduplication whenever duplicate
processing is harmful.

---

**See also:** [Manual Ack](manual-ack.md) · [Reconnection](reconnection.md) · [Backpressure](backpressure.md)
