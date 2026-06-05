# Design: bounded CONNACK wait (`connect_timeout`)

**Date:** 2026-06-05
**Status:** Proposed
**Target:** zmqtt (this repo). A follow-up FastStream change is analysed in §8 but is out of scope for this PR.

## 1. Problem

`MQTTProtocol.connect()` waits for the broker's CONNACK with an **unbounded** read:

```python
# src/zmqtt/_internal/protocol.py
async def connect(self, packet: Connect) -> ConnAck:
    await self._send(self._encode(packet))
    while True:
        data = await self._transport.read(4096)   # <-- no timeout
        self._buf.feed(data)
        ...
```

If a broker accepts the TCP connection but never sends CONNACK — an overloaded
broker, a half-open NAT/load-balancer path, a stalled auth backend — this awaits
forever. There is no protocol-layer rescue at this point: the read loop and
`_cancel_pending()` machinery (`MQTTProtocol.run()`) only start **after**
`connect()` returns, so nothing fails the in-flight read.

Worse, `connect()` runs inside `MQTTClient._connect_with_retry()`, whose retry
loop only advances when `_connect()` *returns or raises*. A hung CONNACK read
therefore wedges the entire reconnect loop on a single attempt — no backoff, no
`max_attempts` give-up, no recovery. The only escape is killing the process.

### Observed incident

A downstream service (a FastStream MQTT→Kafka bridge) saw a ~6 minute broker
blip turn into a hard outage that required a manual restart. The TCP-level
failures (`ConnectionRefusedError`) were handled by the existing retry loop, but
the design review surfaced this adjacent latent hang: had the broker accepted
TCP without completing the MQTT handshake, the client would have wedged
indefinitely.

## 2. Specification & prior art

**MQTT 5.0 (OASIS), §3.2 / §3.1.4 CONNECT actions:**

> "If the Client does not receive a CONNACK packet from the Server within a
> reasonable amount of time, the Client SHOULD close the Network Connection."
> "A 'reasonable' amount of time depends on the type of application and the
> communications infrastructure."

The spec **recommends** a client-side CONNACK timeout and **deliberately leaves
the value application-dependent** — which is why it must be configurable, not
hardcoded.

Every mature client implements this:

| Client | CONNACK-wait timeout | Default | Notes |
|--------|:--:|:--:|-------|
| HiveMQ (Java) | dedicated | **60 s** (`DEFAULT_MQTT_CONNECT_TIMEOUT_MS`) | Separate handler; on fire → disconnect `PROTOCOL_ERROR` "Timeout while waiting for CONNACK". Distinct from socket timeout (`DEFAULT_SOCKET_CONNECT_TIMEOUT_MS = 10 s`). |
| MQTT.js (JS) | yes | **30 s** (`connectTimeout`) | Documented verbatim as "time to wait before a CONNACK is received". |
| Eclipse Paho (Java) | yes | **30 s** (`connectionTimeout`) | Bundles socket + CONNACK (less granular). |
| zmqtt (today) | **none** | — | The gap this design closes. |

Two takeaways:
1. The CONNACK-wait timeout is consistently treated as **distinct from the
   socket-connect timeout** (HiveMQ keeps two separate constants). This maps
   exactly onto zmqtt's existing transport/protocol separation
   (`transport_factory` vs `MQTTProtocol`).
2. Defaults cluster at **30 s** (HiveMQ is more generous at 60 s for this
   specific window).

## 3. Approach (chosen) — protocol-layer CONNACK timeout

Bound **only the CONNACK wait**, at the protocol layer, mirroring the one
existing zmqtt operation that already bounds a broker reply: `ping()`.

### Why this is the faithful zmqtt pattern

zmqtt has two acknowledgement patterns:

- **In-session acks** (`publish` QoS 1/2 → PUBACK/PUBCOMP, `subscribe` → SUBACK,
  `unsubscribe` → UNSUBACK): register an `asyncio.Future` in session state, send
  the packet, `await future` **with no per-op timeout**; the concurrent
  `_read_loop` resolves the future and `_cancel_pending()` fails it on
  disconnect. **This pattern is unavailable during the handshake** — the read
  loop is not running yet.
- **PINGRESP** (`MQTTProtocol.ping()`): the *only* operation that bounds a reply
  with an explicit `asyncio.wait_for(...)` → `MQTTTimeoutError`, because ping is
  the bounded liveness probe with no read loop guaranteeing progress.

CONNACK is structurally in the same position as ping (a reply awaited inline,
pre-read-loop), so it should mirror **ping**: an explicit timeout raising the
**same `MQTTTimeoutError`**. The smoking-gun signal is that
`MQTTProtocol.__init__` already carries `ping_timeout: float = 10.0` — a
`connect_timeout` sibling sitting beside it is exactly the shape the author
would reach for.

Bundling a transport/TCP-connect timeout into this change (the broader
alternative) is explicitly **rejected**: it violates zmqtt's transport/protocol
layering, is a separate concern (the pluggable `transport_factory`), and matches
neither the spec's CONNACK-specific wording nor HiveMQ's two-timeout model.

## 4. Implementation

### 4.1 `src/zmqtt/_internal/protocol.py` — bound the CONNACK wait

Add `connect_timeout` as a sibling of `ping_timeout`, extract the read loop into
a helper, and wrap the **whole** wait (not each `read()`):

```python
def __init__(
    self,
    transport: Transport,
    state: SessionState,
    keepalive: int = 60,
    ping_timeout: float = 10.0,
    connect_timeout: float = 30.0,   # NEW
    version: Literal["3.1.1", "5.0"] = "3.1.1",
) -> None:
    ...
    self._connect_timeout = connect_timeout

async def connect(self, packet: Connect) -> ConnAck:
    """Send CONNECT, read and return CONNACK. Raises on failure.

    Per MQTT 5.0 §3.2, if CONNACK is not received within ``connect_timeout``
    seconds the connection is presumed dead and ``MQTTTimeoutError`` is raised
    (mirrors ``ping()``'s PINGRESP timeout).
    """
    log.debug("Connecting", extra={"client_id": packet.client_id})
    await self._send(self._encode(packet))
    try:
        return await asyncio.wait_for(
            self._await_connack(), timeout=self._connect_timeout
        )
    except asyncio.TimeoutError as e:
        msg = "CONNACK not received within timeout"
        raise MQTTTimeoutError(msg) from e

async def _await_connack(self) -> ConnAck:
    while True:
        data = await self._transport.read(4096)
        self._buf.feed(data)
        for pkt in self._buf:
            if not isinstance(pkt, ConnAck):
                raise MQTTProtocolError(f"Expected CONNACK, got {pkt!r}")
            if pkt.return_code != 0:
                raise MQTTConnectError(pkt.return_code)
            log.info("Connected", extra={"session_present": pkt.session_present})
            return pkt
```

Wrapping the *whole* wait is deliberate: wrapping each individual `read()` would
let a slow byte-trickle reset the deadline indefinitely.

### 4.2 `src/zmqtt/client.py` — thread the parameter through

- `MQTTClient.__init__`: add `connect_timeout: float = 30.0`, store
  `self._connect_timeout`, and pass it when constructing `MQTTProtocol` in
  `_connect()`:

  ```python
  protocol = MQTTProtocol(
      transport,
      SessionState(),
      keepalive=self._keepalive,
      connect_timeout=self._connect_timeout,   # NEW
      version=self._version,
  )
  ```

- `create_client()`: add `connect_timeout: float = 30.0` to the function and
  **both** `@overload` signatures, and forward it to `MQTTClient(...)`.

### 4.3 `src/zmqtt/client.py` — make the timeout retryable (the crux)

`_connect_with_retry()` currently retries only on `OSError`. A CONNACK timeout
raises `MQTTTimeoutError`, which is neither `MQTTConnectError` (re-raised) nor
`OSError` — so without this change it would propagate and *give up immediately*,
trading "hang forever" for "fail instantly". Treat it as retryable:

```python
except (OSError, MQTTTimeoutError):   # was: except OSError
    attempt += 1
    ...
```

`MQTTTimeoutError` is **already imported** in `client.py` (it is caught by
`_run_loop()`), so no import change is needed.

**Consistency note:** `_run_loop()` already catches `MQTTTimeoutError` as a
reconnect trigger. This change extends the same "a timeout is reconnect-worthy"
semantics to the initial/retry connect path, rather than introducing new
behaviour.

## 5. Behaviour & edge cases

- **Resource cleanup:** on timeout, `wait_for` cancels `_await_connack()` and its
  in-flight `read`. `MQTTClient._connect()`'s existing
  `except BaseException: await transport.close()` releases the socket fd. No leak.
- **Python 3.10 vs 3.11+:** `asyncio.wait_for` raises `asyncio.TimeoutError`,
  which is the builtin `TimeoutError` (an `OSError` subclass) on 3.11+ but
  `concurrent.futures.TimeoutError` (not an `OSError`) on 3.10. We convert it to
  `MQTTTimeoutError` at the `connect()` boundary, so `_connect_with_retry()` only
  ever sees `MQTTTimeoutError` — the version difference is fully contained.
  (zmqtt supports >=3.10.)
- **Reconnect interaction:** timeout → `MQTTTimeoutError` → retry with backoff.
  With `ReconnectConfig(max_attempts=None)` it retries forever; with the default
  `max_attempts=5` it gives up after 5 — both strictly better than hanging.
- **`connect_timeout <= 0`:** `wait_for` would fail immediately. Document that the
  value must be positive (optional `ValueError` guard in `MQTTClient.__init__`).
- **Backward compatibility:** the finite default changes behaviour only for the
  pathological "broker never CONNACKs" case (previously infinite hang → now a
  bounded, retryable failure). Normal connects are unaffected.

## 6. Tests (`tests/test_protocol.py`, plus existing seams)

Use the documented `transport_factory` override (and the existing
`tests/conftest.py` fixtures) with small timeouts and a fake transport whose
`read()` blocks on a never-set `asyncio.Event`, so tests are fast and
deterministic:

1. `connect()` raises `MQTTTimeoutError` when no CONNACK arrives within
   `connect_timeout`.
2. `connect()` still succeeds normally when CONNACK arrives in time (regression).
3. `MQTTClient._connect_with_retry()` retries past a connect timeout and then
   succeeds (fake transport: time out once, then connect).
4. The transport is closed after a connect timeout (no fd leak).
5. Default `connect_timeout` is `30.0` on both `MQTTClient` and `create_client`.

The real-broker integration suites under `tests/test_brokers/` (mosquitto,
hivemq, nanomq, artemis) need no change — the timeout is exercised at the unit
level.

## 7. Docs

- `docs/connecting.md`: document the new `connect_timeout` parameter and its
  30 s default.
- `docs/error-handling.md`: note that `connect()` raises `MQTTTimeoutError` if
  CONNACK does not arrive in time (and that reconnection treats it as retryable).
- **CHANGELOG.md is auto-generated** by python-semantic-release from conventional
  commits — do **not** hand-edit it. The PR's `feat:` commit message produces the
  entry.

## 8. FastStream surfacing (follow-up, gated — out of scope here)

FastStream's `MQTTBroker` wraps this client. Once this PR is released, a
follow-up FastStream change surfaces the knob:

- `MQTTBroker.__init__` adds `connect_timeout: float = 30.0` and forwards it via
  `super().__init__(...)` into `_connection_kwargs`, which already become
  `MQTTClient(**self._connection_kwargs)` — the same mechanism `reconnect` and
  `session_expiry_interval` use today.
- **Sequencing:** zmqtt PR merges → zmqtt release (new version) → FastStream
  bumps its zmqtt pin and adds the parameter. Two coordinated PRs across two
  repos in the same org.
- Result: the downstream bridge sets `connect_timeout` explicitly, completing the
  resilience story alongside the `reconnect=ReconnectConfig(max_attempts=None)`
  it already configures.

## 9. Out of scope

- Transport/TCP socket-connect timeout (a `transport_factory` concern; HiveMQ
  keeps this separate at 10 s). Could be a later, independent enhancement.
- Exposing `ping_timeout` through `MQTTClient` / `create_client` / FastStream.
- Any change to reconnection defaults.
