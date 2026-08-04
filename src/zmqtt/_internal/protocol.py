"""Protocol engine: ties codec, transport, and session state together."""

import asyncio
import contextlib
import dataclasses
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable
from typing import Final, Literal, cast

from zmqtt._internal import topic_matching
from zmqtt._internal.packets.auth import Auth
from zmqtt._internal.packets.codec import AnyPacket, encode
from zmqtt._internal.packets.connect import ConnAck, Connect
from zmqtt._internal.packets.disconnect import Disconnect
from zmqtt._internal.packets.ping import PingReq, PingResp
from zmqtt._internal.packets.properties import SubscribeProperties
from zmqtt._internal.packets.publish import PubAck, PubComp, Publish, PubRec, PubRel
from zmqtt._internal.packets.reader import PacketBuffer
from zmqtt._internal.packets.subscribe import (
    SubAck,
    Subscribe,
    SubscriptionRequest,
    UnsubAck,
    Unsubscribe,
)
from zmqtt._internal.state import (
    InboundQoS2Flight,
    InboundQoS2State,
    OutboundQoS2Flight,
    OutboundQoS2State,
    QoS1Flight,
    SessionState,
)
from zmqtt._internal.subscription_index import SubscriptionEntry, SubscriptionSelection
from zmqtt._internal.transport.base import Transport
from zmqtt._internal.types.message import Message
from zmqtt._internal.types.qos import QoS
from zmqtt.errors import (
    MQTTConnectError,
    MQTTDisconnectedError,
    MQTTProtocolError,
    MQTTSubscribeError,
    MQTTTimeoutError,
)

log = logging.getLogger("zmqtt.protocol")


class _SubscriptionGuard:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.users = 0


class _SubscriptionGuards:
    """Serialize ownership changes only when topic filters overlap exactly."""

    def __init__(self) -> None:
        self._guards: dict[str, _SubscriptionGuard] = {}

    @contextlib.asynccontextmanager
    async def hold(self, filters: Iterable[str]) -> AsyncGenerator[None]:
        entries: list[tuple[str, _SubscriptionGuard]] = []
        for filter_ in sorted(set(filters)):
            guard = self._guards.setdefault(filter_, _SubscriptionGuard())
            guard.users += 1
            entries.append((filter_, guard))

        acquired = 0
        try:
            for _, guard in entries:
                await guard.lock.acquire()
                acquired += 1
            yield
        finally:
            for _, guard in reversed(entries[:acquired]):
                guard.lock.release()
            for filter_, guard in entries:
                guard.users -= 1
                if guard.users == 0:
                    self._guards.pop(filter_, None)


def _raise_on_rejected_filters(filters: list[SubscriptionRequest], suback: SubAck) -> None:
    """Surface SUBACK failure codes (>= 0x80) instead of ignoring them.

    A rejected filter — most commonly an authorization denial — otherwise looks
    exactly like a successful subscription and silently never receives anything.
    Return codes map to filters by position (MQTT 3.1.1 §3.9.3, 5.0 §3.9.2.1).
    """
    # strict=False: a non-conforming broker answering with a short code list
    # should not turn into a ValueError here.
    failures = {req.topic_filter: code for req, code in zip(filters, suback.return_codes, strict=False) if code >= 0x80}
    if failures:
        raise MQTTSubscribeError(failures)


class MQTTProtocol:
    """MQTT protocol engine.

    Lifecycle:
      1. ``await protocol.connect(packet)``  — handshake, returns ConnAck
      2. ``await protocol.run()``            — read loop + ping loop (runs until disconnect)
      3. ``await protocol.disconnect()``     — clean shutdown

    QoS flows and subscription management are available between steps 1 and 3.
    """

    def __init__(
        self,
        transport: Transport,
        state: SessionState,
        keepalive: int = 60,
        ping_timeout: float = 10.0,
        connect_timeout: float = 30.0,
        version: Literal["3.1.1", "5.0"] = "3.1.1",
        stripped_prefixes: tuple[str, ...] = topic_matching._DEFAULT_STRIPPED_PREFIXES,
        response_observer: Callable[[Message], None] | None = None,
    ) -> None:
        self._transport = transport
        self._state = state
        self._keepalive = keepalive
        self._ping_timeout = ping_timeout
        self._connect_timeout = connect_timeout
        self._version: Final = version
        self._stripped_prefixes = stripped_prefixes
        self._response_observer = response_observer
        self._buf = PacketBuffer(version=version)
        self._ping_waiters: list[asyncio.Future[None]] = []
        self._subscription_guards = _SubscriptionGuards()
        self._disconnecting = False
        self._dead = False
        self.started_event = asyncio.Event()

    async def connect(self, packet: Connect) -> ConnAck:
        """Send CONNECT, read and return CONNACK. Raises on failure.

        Per MQTT 5.0 section 3.2, if the CONNACK is not received within
        ``connect_timeout`` seconds the connection is presumed dead and
        ``MQTTTimeoutError`` is raised (mirrors ``ping()``'s PINGRESP timeout).
        """
        log.debug("Connecting", extra={"client_id": packet.client_id})
        await self._send(self._encode(packet))
        try:
            # No asyncio.shield (unlike ping): on timeout the transport is closed and
            # replaced by _connect_with_retry, so keeping the read coroutine alive is pointless.
            return await asyncio.wait_for(self._await_connack(), timeout=self._connect_timeout)
        except asyncio.TimeoutError as e:
            msg = "CONNACK not received within timeout"
            raise MQTTTimeoutError(msg) from e

    async def _await_connack(self) -> ConnAck:
        while True:
            data = await self._transport.read(4096)
            self._buf.feed(data)
            for pkt in self._buf:
                if not isinstance(pkt, ConnAck):
                    msg = f"Expected CONNACK, got {pkt!r}"
                    raise MQTTProtocolError(msg)
                if pkt.return_code != 0:
                    raise MQTTConnectError(pkt.return_code)
                log.info(
                    "Connected",
                    extra={"session_present": pkt.session_present},
                )
                return pkt

    async def run(self) -> None:
        """Run read loop and ping loop concurrently until disconnection."""
        read_task = asyncio.create_task(self._read_loop())
        ping_task = asyncio.create_task(self._ping_loop())
        self.started_event.set()
        try:
            await asyncio.gather(read_task, ping_task)
        except BaseException:
            read_task.cancel()
            ping_task.cancel()
            await asyncio.gather(read_task, ping_task, return_exceptions=True)
            raise
        finally:
            self.started_event.clear()
            self._dead = True
            self._cancel_pending()

    async def disconnect(self) -> None:
        """Send DISCONNECT and close the transport."""
        self._disconnecting = True
        with contextlib.suppress(Exception):
            await self._send(self._encode(Disconnect()))
        await self._transport.close()
        log.info("Disconnected")

    def _cancel_pending(self) -> None:  # noqa: C901
        """Fail all futures awaiting broker responses — called when run() exits."""
        exc = MQTTDisconnectedError("Connection lost")
        for sub_f in self._state.pending_subs.values():
            if not sub_f.done():
                sub_f.set_exception(exc)
        for unsub_f in self._state.pending_unsubs.values():
            if not unsub_f.done():
                unsub_f.set_exception(exc)
        for q1_flight in self._state.inflight_qos1.values():
            if not q1_flight.future.done():
                q1_flight.future.set_exception(exc)
        for q2_flight in self._state.inflight_qos2_out.values():
            if not q2_flight.future.done():
                q2_flight.future.set_exception(exc)
        for ping_f in self._ping_waiters:
            if not ping_f.done():
                ping_f.set_exception(exc)
        self._ping_waiters.clear()

    def _ensure_alive(self) -> None:
        """Refuse new operations once the run loop has exited.

        _cancel_pending() fails every future that existed when the loop died, but
        an operation started afterwards would create a fresh future that nothing
        will ever resolve — the caller would hang forever (a dead client used to
        hang even in __aexit__, inside unsubscribe()).
        """
        if self._dead:
            msg = "Connection lost"
            raise MQTTDisconnectedError(msg)

    async def publish(self, packet: Publish) -> PubAck | PubComp | None:
        """
        Publish a message. Returns PubAck (QoS 1), PubComp (QoS 2), or None (QoS 0).
        """
        self._ensure_alive()
        match packet.qos:
            case QoS.AT_MOST_ONCE:
                await self._send(self._encode(packet))
                log.debug("Published QoS 0", extra={"topic": packet.topic})
                return None

            case QoS.AT_LEAST_ONCE:
                loop = asyncio.get_running_loop()
                pid = self._state.packet_ids.acquire()
                packet = dataclasses.replace(packet, packet_id=pid)
                future: asyncio.Future[PubAck] = loop.create_future()
                self._state.inflight_qos1[pid] = QoS1Flight(
                    packet_id=pid,
                    publish=packet,
                    future=future,
                )
                await self._send(self._encode(packet))
                log.debug(
                    "Published QoS 1",
                    extra={"topic": packet.topic, "packet_id": pid},
                )
                return await future

            case QoS.EXACTLY_ONCE:
                loop = asyncio.get_running_loop()
                pid = self._state.packet_ids.acquire()
                packet = dataclasses.replace(packet, packet_id=pid)
                future2: asyncio.Future[PubComp] = loop.create_future()
                self._state.inflight_qos2_out[pid] = OutboundQoS2Flight(
                    packet_id=pid,
                    publish=packet,
                    state=OutboundQoS2State.PENDING_PUBREC,
                    future=future2,
                )
                await self._send(self._encode(packet))
                log.debug(
                    "Published QoS 2",
                    extra={"topic": packet.topic, "packet_id": pid},
                )
                return await future2

    async def subscribe(
        self,
        filters: list[SubscriptionRequest],
        *,
        auto_ack: bool = True,
        queue_maxsize: int = 0,
        subscription_identifier: int | None = None,
    ) -> tuple[SubAck, dict[str, asyncio.Queue[Message]]]:
        """Send SUBSCRIBE and return (SubAck, {filter: queue}) after broker ACK.

        Queues are registered before SUBSCRIBE is sent so no messages are lost.
        Duplicate filters (already subscribed) are logged as warnings and skipped;
        they are still included in the SUBSCRIBE packet sent to the broker.

        ``queue_maxsize`` bounds the per-filter delivery queue. When it is full,
        ``_deliver`` blocks, which stalls the read loop and ultimately pushes back
        on the broker through the TCP window — the flow-control chain the docs
        describe. ``0`` keeps the queue unbounded.

        ``subscription_identifier`` (MQTT 5) is sent in the SUBSCRIBE properties;
        the broker echoes it on every PUBLISH this subscription causes, which lets
        ``_deliver`` attribute the message to the exact subscription that matched
        instead of guessing by filter specificity.
        """
        async with self._subscription_guards.hold(req.topic_filter for req in filters):
            return await self._subscribe(
                filters,
                auto_ack=auto_ack,
                queue_maxsize=queue_maxsize,
                subscription_identifier=subscription_identifier,
            )

    async def _subscribe(
        self,
        filters: list[SubscriptionRequest],
        *,
        auto_ack: bool,
        queue_maxsize: int,
        subscription_identifier: int | None,
    ) -> tuple[SubAck, dict[str, asyncio.Queue[Message]]]:
        new_entries: dict[str, SubscriptionEntry] = {}

        for req in filters:
            f = req.topic_filter
            if self._state.subscriptions.contains(f):
                log.warning("Filter %r already subscribed (ignored)", f)
            else:
                new_entries[f] = SubscriptionEntry(
                    queue=asyncio.Queue(queue_maxsize),
                    auto_ack=auto_ack,
                    actual_filter=topic_matching._shared_filter_to_actual(f, self._stripped_prefixes),
                    subscription_identifier=subscription_identifier,
                )

        self._state.subscriptions.add_many(new_entries)
        subscribed = False
        try:
            suback = await self._send_subscribe(filters, subscription_identifier)
            subscribed = True
            return suback, {f: entry.queue for f, entry in new_entries.items()}
        finally:
            if not subscribed:
                for f in new_entries:
                    self._state.subscriptions.remove(f)

    async def unsubscribe(self, filters: list[str]) -> UnsubAck | None:
        """Remove queues and unsubscribe filters without response observers.

        Returns ``None`` when every broker subscription must stay active for
        request/response routing.
        """
        async with self._subscription_guards.hold(filters):
            return await self._unsubscribe(filters)

    async def _unsubscribe(self, filters: list[str]) -> UnsubAck | None:
        self._ensure_alive()
        observed_filters = [f for f in filters if self._state.subscriptions.has_response_observer(f)]
        broker_filters = [f for f in filters if f not in observed_filters]
        for f in filters:
            self._state.subscriptions.remove(f)

        unsuback = await self._send_unsubscribe(broker_filters) if broker_filters else None
        if observed_filters:
            requests = [SubscriptionRequest(topic_filter=f, qos=QoS.AT_MOST_ONCE) for f in observed_filters]
            await self._send_subscribe(requests, subscription_identifier=None)
        return unsuback

    async def add_response_observer(self, topic: str) -> None:
        """Keep an exact response topic subscribed without claiming its messages."""
        async with self._subscription_guards.hold([topic]):
            await self._add_response_observer(topic)

    async def _add_response_observer(self, topic: str) -> None:
        self._ensure_alive()
        if not self._state.subscriptions.add_response_observer(topic):
            return
        if self._state.subscriptions.contains(topic):
            return
        request = SubscriptionRequest(topic_filter=topic, qos=QoS.AT_MOST_ONCE)
        subscribed = False
        try:
            await self._send_subscribe([request], subscription_identifier=None)
            subscribed = True
        finally:
            if not subscribed:
                self._state.subscriptions.remove_response_observer(topic)

    async def remove_response_observer(self, topic: str) -> None:
        """Release an exact response topic unless an application owns it."""
        async with self._subscription_guards.hold([topic]):
            await self._remove_response_observer(topic)

    async def _remove_response_observer(self, topic: str) -> None:
        if not self._state.subscriptions.remove_response_observer(topic):
            return
        if self._state.subscriptions.contains(topic) or self._dead:
            return
        await self._send_unsubscribe([topic])

    async def _send_subscribe(
        self,
        filters: list[SubscriptionRequest],
        subscription_identifier: int | None,
    ) -> SubAck:
        self._ensure_alive()
        loop = asyncio.get_running_loop()
        pid = self._state.packet_ids.acquire()
        future: asyncio.Future[SubAck] = loop.create_future()
        self._state.pending_subs[pid] = future
        properties = (
            SubscribeProperties(subscription_identifier=subscription_identifier)
            if subscription_identifier is not None
            else None
        )
        try:
            await self._send(
                self._encode(
                    Subscribe(packet_id=pid, subscriptions=tuple(filters), properties=properties),
                ),
            )
            log.debug("Sent SUBSCRIBE", extra={"packet_id": pid})
            suback = await future
            _raise_on_rejected_filters(filters, suback)
            return suback
        finally:
            self._state.pending_subs.pop(pid, None)
            self._state.packet_ids.release(pid)

    async def _send_unsubscribe(self, filters: list[str]) -> UnsubAck:
        self._ensure_alive()
        pid = self._state.packet_ids.acquire()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[UnsubAck] = loop.create_future()
        self._state.pending_unsubs[pid] = future
        try:
            await self._send(
                encode(
                    Unsubscribe(packet_id=pid, topic_filters=tuple(filters)),
                    version=self._version,
                ),
            )
            log.debug("Sent UNSUBSCRIBE", extra={"packet_id": pid})
            return await future
        finally:
            self._state.pending_unsubs.pop(pid, None)
            self._state.packet_ids.release(pid)

    async def ping(self, timeout: float | None = None) -> float:
        """Send PINGREQ and return RTT in seconds when PINGRESP is received."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        self._ping_waiters.append(future)
        t0 = loop.time()
        await self._send(self._encode(PingReq()))
        log.debug("Sent PINGREQ")
        try:
            await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except asyncio.TimeoutError as e:
            self._ping_waiters.remove(future)
            msg = "PINGRESP not received within timeout"
            raise MQTTTimeoutError(msg) from e
        return loop.time() - t0

    async def send_auth(self, packet: Auth) -> None:
        """Send an AUTH packet (MQTT 5.0 enhanced authentication)."""
        if self._version != "5.0":
            msg = f"Feature is not supported for mqtt protocol version {self._version}"
            raise RuntimeError(
                msg,
            )
        await self._send(self._encode(packet))
        log.debug("Sent AUTH", extra={"reason_code": packet.reason_code})

    async def _read_loop(self) -> None:
        while True:
            try:
                data = await self._transport.read(4096)
            except MQTTDisconnectedError:
                if self._disconnecting:
                    return
                raise
            self._buf.feed(data)
            for packet in self._buf:
                await self._dispatch(packet)

    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(self._keepalive)
            await self.ping(timeout=self._ping_timeout)

    async def _dispatch(self, packet: AnyPacket) -> None:  # noqa: C901
        log.debug("Received %r", packet)
        match packet:
            case Publish():
                await self._handle_publish(packet)
            case PubAck():
                await self._handle_puback(packet)
            case PubRec():
                await self._handle_pubrec(packet)
            case PubRel():
                await self._handle_pubrel(packet)
            case PubComp():
                await self._handle_pubcomp(packet)
            case SubAck():
                await self._handle_suback(packet)
            case UnsubAck():
                await self._handle_unsuback(packet)
            case PingResp():
                self._handle_pingresp()
            case Disconnect(reason_code=reason_code):
                # A broker-initiated DISCONNECT (session takeover, keepalive timeout,
                # admin kick) is a disconnection, not a protocol violation — raise it
                # as MQTTDisconnectedError so it takes the reconnect path.
                msg = f"Broker sent DISCONNECT (reason code 0x{reason_code:02X})"
                raise MQTTDisconnectedError(msg)
            case Auth():
                if self._version != "5.0":
                    msg = "Received AUTH packet in MQTT 3.1.1 session"
                    raise MQTTProtocolError(
                        msg,
                    )
                # AUTH exchange is handled by the caller via auth(); ignore here.
            case _:
                msg = f"Unexpected packet from broker: {packet!r}"
                raise MQTTProtocolError(msg)

    def _select_subscription(self, publish: Publish) -> SubscriptionSelection:
        identifier = publish.properties.subscription_identifier if publish.properties else None
        if identifier is None:
            return self._state.subscriptions.select(topic=publish.topic)
        return self._state.subscriptions.select_by_identifier(
            topic=publish.topic,
            identifier=identifier,
        )

    def _should_auto_ack(self, publish: Publish) -> bool:
        """Return True if the subscription selected for this PUBLISH uses auto-ack."""
        selection = self._select_subscription(publish)
        return selection.recipient is None or selection.recipient[1].auto_ack

    async def _handle_publish(self, packet: Publish) -> None:
        if packet.qos is QoS.AT_MOST_ONCE:
            return await self._deliver(packet, ack_callback=None)
        if packet.qos is QoS.AT_LEAST_ONCE:
            return await self._handle_qos1_publish(packet)
        if packet.qos is QoS.EXACTLY_ONCE:
            return await self._handle_qos2_publish(packet)
        return None

    async def _handle_qos1_publish(self, packet: Publish) -> None:
        if packet.packet_id is None:
            msg = "Cannot publish without packet id"
            raise ValueError(msg)
        if self._should_auto_ack(packet):
            await self._send(self._encode(PubAck(packet_id=packet.packet_id)))
            await self._deliver(packet, ack_callback=None)
        else:
            acked = False

            async def _puback() -> None:
                nonlocal acked
                if acked:
                    return
                acked = True
                await self._send(self._encode(PubAck(packet_id=cast("int", packet.packet_id))))

            await self._deliver(packet, ack_callback=_puback)

    async def _handle_qos2_publish(self, packet: Publish) -> None:
        if packet.packet_id is None:
            msg = "Cannot publish without packet id"
            raise ValueError(msg)
        if packet.packet_id in self._state.inflight_qos2_in:
            # PUBREC already sent — resend it (duplicate PUBLISH after PUBREC)
            await self._send(self._encode(PubRec(packet_id=packet.packet_id)))
            return
        if packet.packet_id in self._state.pending_ack_qos2_in:
            # Duplicate PUBLISH while app hasn't called msg.ack() yet — ignore
            return
        if self._should_auto_ack(packet):
            self._state.inflight_qos2_in[packet.packet_id] = InboundQoS2Flight(
                packet_id=packet.packet_id,
                publish=packet,
                state=InboundQoS2State.PENDING_PUBREL,
            )
            await self._send(self._encode(PubRec(packet_id=packet.packet_id)))
        else:
            self._state.pending_ack_qos2_in.add(packet.packet_id)
            pid = packet.packet_id

            async def _pubrec() -> None:
                self._state.pending_ack_qos2_in.discard(pid)
                self._state.inflight_qos2_in[pid] = InboundQoS2Flight(
                    packet_id=pid,
                    publish=packet,
                    state=InboundQoS2State.PENDING_PUBREL,
                )
                await self._send(self._encode(PubRec(packet_id=pid)))

            await self._deliver(packet, ack_callback=_pubrec)

    async def _handle_pubrel(self, packet: PubRel) -> None:
        flight = self._state.inflight_qos2_in.pop(packet.packet_id, None)
        if flight is None:
            msg = f"PUBREL for unknown packet_id {packet.packet_id}"
            raise MQTTProtocolError(msg)
        await self._send(self._encode(PubComp(packet_id=packet.packet_id)))
        await self._deliver(flight.publish, ack_callback=None)

    async def _handle_puback(self, packet: PubAck) -> None:
        flight = self._state.inflight_qos1.pop(packet.packet_id, None)
        if flight is None:
            msg = f"PUBACK for unknown packet_id {packet.packet_id}"
            raise MQTTProtocolError(msg)
        self._state.packet_ids.release(packet.packet_id)
        flight.future.set_result(packet)
        log.debug("QoS 1 ack received", extra={"packet_id": packet.packet_id})

    async def _handle_pubrec(self, packet: PubRec) -> None:
        flight = self._state.inflight_qos2_out.get(packet.packet_id)
        if flight is None:
            msg = f"PUBREC for unknown packet_id {packet.packet_id}"
            raise MQTTProtocolError(msg)
        if flight.state is not OutboundQoS2State.PENDING_PUBREC:
            msg = f"PUBREC in wrong state {flight.state} for packet_id {packet.packet_id}"
            raise MQTTProtocolError(
                msg,
            )
        flight.state = OutboundQoS2State.PENDING_PUBCOMP
        await self._send(self._encode(PubRel(packet_id=packet.packet_id)))
        log.debug(
            "QoS 2 PUBREC received, sent PUBREL",
            extra={"packet_id": packet.packet_id},
        )

    async def _handle_pubcomp(self, packet: PubComp) -> None:
        flight = self._state.inflight_qos2_out.pop(packet.packet_id, None)
        if flight is None:
            msg = f"PUBCOMP for unknown packet_id {packet.packet_id}"
            raise MQTTProtocolError(msg)
        self._state.packet_ids.release(packet.packet_id)
        flight.future.set_result(packet)
        log.debug("QoS 2 complete", extra={"packet_id": packet.packet_id})

    async def _handle_suback(self, packet: SubAck) -> None:
        future = self._state.pending_subs.get(packet.packet_id)
        if future is None:
            msg = f"SUBACK for unknown packet_id {packet.packet_id}"
            raise MQTTProtocolError(msg)
        future.set_result(packet)

    async def _handle_unsuback(self, packet: UnsubAck) -> None:
        future = self._state.pending_unsubs.get(packet.packet_id)
        if future is None:
            msg = f"UNSUBACK for unknown packet_id {packet.packet_id}"
            raise MQTTProtocolError(
                msg,
            )
        future.set_result(packet)

    def _handle_pingresp(self) -> None:
        if self._ping_waiters:
            f = self._ping_waiters.pop(0)
            if not f.done():
                f.set_result(None)
        log.debug("PINGRESP received")

    def _encode(self, packet: AnyPacket) -> bytes:
        return encode(packet, version=self._version)

    async def _send(self, data: bytes) -> None:
        log.debug("Sending %d bytes", len(data))
        await self._transport.write(data)

    async def _deliver(
        self,
        publish: Publish,
        ack_callback: Callable[[], Awaitable[None]] | None,
    ) -> None:
        selection = self._select_subscription(publish)
        has_response_observer = self._state.subscriptions.has_response_observer(publish.topic)
        if has_response_observer and self._response_observer is not None:
            self._response_observer(self._make_message(publish))
        if selection.identifier_missing:
            identifier = publish.properties.subscription_identifier if publish.properties else None
            log.warning(
                "No subscription with identifier %r for topic %r, falling back to filter matching",
                identifier,
                publish.topic,
            )
        if selection.tied_filters:
            log.warning(
                "Multiple equally-specific subscribers for %r: %s, delivering to first",
                publish.topic,
                list(selection.tied_filters),
            )

        recipient = selection.recipient
        if recipient is None:
            if not has_response_observer:
                log.warning("No subscriber for topic %r", publish.topic)
            return
        await self._put_message(publish, [recipient], ack_callback)

    def _make_message(self, publish: Publish) -> Message:
        return Message(
            topic=publish.topic,
            payload=publish.payload,
            qos=publish.qos,
            retain=publish.retain,
            properties=publish.properties,
        )

    async def _put_message(
        self,
        publish: Publish,
        recipients: list[tuple[str, SubscriptionEntry]],
        ack_callback: Callable[[], Awaitable[None]] | None,
    ) -> None:
        for filter_, entry in recipients:
            msg = self._make_message(publish)
            if not entry.auto_ack and ack_callback is not None:
                msg._ack_callback = ack_callback
            await entry.queue.put(msg)
            log.debug(
                "Delivered message",
                extra={"topic": publish.topic, "filter": filter_},
            )
