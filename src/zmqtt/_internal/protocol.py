"""Protocol engine: ties codec, transport, and session state together."""

import asyncio
import contextlib
import dataclasses
import logging
from collections.abc import Awaitable, Callable
from typing import Final, Literal, cast

from zmqtt._internal.packets.auth import Auth
from zmqtt._internal.packets.codec import AnyPacket, encode
from zmqtt._internal.packets.connect import ConnAck, Connect
from zmqtt._internal.packets.disconnect import Disconnect
from zmqtt._internal.packets.ping import PingReq, PingResp
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
    SubscriptionEntry,
)
from zmqtt._internal.transport.base import Transport
from zmqtt._internal.types.message import Message
from zmqtt._internal.types.qos import QoS
from zmqtt.errors import (
    MQTTConnectError,
    MQTTDisconnectedError,
    MQTTProtocolError,
    MQTTTimeoutError,
)

log = logging.getLogger(__name__)


def _shared_filter_to_actual(filter_: str) -> str:
    """Strip the $share/<group>/ prefix from a shared subscription filter."""
    if filter_.startswith("$share/"):
        parts = filter_.split("/", 2)
        if len(parts) == 3:
            return parts[2]
    return filter_


def _topic_matches(actual_filter: str, topic: str) -> bool:
    """Return True if topic matches the MQTT topic filter (shared prefix already stripped)."""
    # $-prefixed topics are not matched by wildcards unless filter also starts with $
    if topic.startswith("$") and not actual_filter.startswith("$"):
        return False
    return _match_parts(actual_filter.split("/"), topic.split("/"))


def _match_parts(fparts: list[str], tparts: list[str]) -> bool:
    if not fparts:
        return not tparts
    if fparts[0] == "#":
        return True
    if not tparts:
        return False
    if fparts[0] != "+" and fparts[0] != tparts[0]:
        return False
    return _match_parts(fparts[1:], tparts[1:])


def _segment_rank(seg: str) -> int:
    if seg == "#":
        return 2
    if seg == "+":
        return 1
    return 0


def _filter_specificity(actual_filter: str) -> tuple[int, ...]:
    """Return a sort key for a filter (shared prefix already stripped); lexicographically smaller == more specific."""
    return tuple(_segment_rank(s) for s in actual_filter.split("/"))


@dataclasses.dataclass(slots=True)
class ProtocolConfig:
    keepalive: int = 60
    ping_timeout: float = 10.0
    retransmit_interval: float = 5.0
    version: Literal["3.1.1", "5.0"] = "3.1.1"


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
        config: ProtocolConfig | None = None,
    ) -> None:
        if config is None:
            config = ProtocolConfig()
        if config.retransmit_interval <= 0:
            msg = "retransmit_interval must be greater than 0"
            raise ValueError(msg)
        self._transport = transport
        self._state = state
        self._keepalive = config.keepalive
        self._ping_timeout = config.ping_timeout
        self._retransmit_interval = config.retransmit_interval
        self._version: Final = config.version
        self._buf = PacketBuffer(version=config.version)
        self._ping_waiters: list[asyncio.Future[None]] = []
        self._disconnecting = False
        self.started_event = asyncio.Event()

    async def connect(self, packet: Connect) -> ConnAck:
        """Send CONNECT, read and return CONNACK. Raises on failure."""
        log.debug("Connecting", extra={"client_id": packet.client_id})
        await self._send(self._encode(packet))
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
            self._cancel_retransmit_task(q1_flight.retransmit_task)
            if not q1_flight.future.done():
                q1_flight.future.set_exception(exc)
        for q2_flight in self._state.inflight_qos2_out.values():
            self._cancel_retransmit_task(q2_flight.retransmit_task)
            if not q2_flight.future.done():
                q2_flight.future.set_exception(exc)
        for ping_f in self._ping_waiters:
            if not ping_f.done():
                ping_f.set_exception(exc)
        self._ping_waiters.clear()

    async def publish(self, packet: Publish) -> PubAck | PubComp | None:
        """
        Publish a message. Returns PubAck (QoS 1), PubComp (QoS 2), or None (QoS 0).
        """
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
                qos1_flight = QoS1Flight(
                    packet_id=pid,
                    publish=packet,
                    future=future,
                )
                self._state.inflight_qos1[pid] = qos1_flight
                qos1_flight.retransmit_task = asyncio.create_task(self._retransmit_qos1_publish(pid))
                try:
                    await self._send(self._encode(packet))
                except BaseException:
                    self._cancel_retransmit_task(qos1_flight.retransmit_task)
                    self._state.inflight_qos1.pop(pid, None)
                    self._state.packet_ids.release(pid)
                    raise
                log.debug(
                    "Published QoS 1",
                    extra={"topic": packet.topic, "packet_id": pid},
                )
                try:
                    return await future
                except BaseException:
                    self._abandon_qos1_publish(qos1_flight)
                    raise

            case QoS.EXACTLY_ONCE:
                loop = asyncio.get_running_loop()
                pid = self._state.packet_ids.acquire()
                packet = dataclasses.replace(packet, packet_id=pid)
                future2: asyncio.Future[PubComp] = loop.create_future()
                qos2_flight = OutboundQoS2Flight(
                    packet_id=pid,
                    publish=packet,
                    state=OutboundQoS2State.PENDING_PUBREC,
                    future=future2,
                )
                self._state.inflight_qos2_out[pid] = qos2_flight
                qos2_flight.retransmit_task = asyncio.create_task(self._retransmit_qos2_publish(pid))
                try:
                    await self._send(self._encode(packet))
                except BaseException:
                    self._cancel_retransmit_task(qos2_flight.retransmit_task)
                    self._state.inflight_qos2_out.pop(pid, None)
                    self._state.packet_ids.release(pid)
                    raise
                log.debug(
                    "Published QoS 2",
                    extra={"topic": packet.topic, "packet_id": pid},
                )
                try:
                    return await future2
                except BaseException:
                    self._abandon_qos2_publish(qos2_flight)
                    raise

    async def subscribe(
        self,
        filters: list[SubscriptionRequest],
        *,
        auto_ack: bool = True,
    ) -> tuple[SubAck, dict[str, asyncio.Queue[Message]]]:
        """Send SUBSCRIBE and return (SubAck, {filter: queue}) after broker ACK.

        Queues are registered before SUBSCRIBE is sent so no messages are lost.
        Duplicate filters (already subscribed) are logged as warnings and skipped;
        they are still included in the SUBSCRIBE packet sent to the broker.
        """
        loop = asyncio.get_running_loop()
        pid = self._state.packet_ids.acquire()
        new_entries: dict[str, SubscriptionEntry] = {}
        for req in filters:
            f = req.topic_filter
            if f in self._state.subscriptions:
                log.warning("Filter %r already subscribed (ignored)", f)
            else:
                new_entries[f] = SubscriptionEntry(
                    queue=asyncio.Queue(),
                    auto_ack=auto_ack,
                    actual_filter=_shared_filter_to_actual(f),
                )
        self._state.subscriptions.update(new_entries)
        future: asyncio.Future[SubAck] = loop.create_future()
        self._state.pending_subs[pid] = future
        await self._send(
            self._encode(Subscribe(packet_id=pid, subscriptions=tuple(filters))),
        )
        log.debug("Sent SUBSCRIBE", extra={"packet_id": pid})
        try:
            suback = await future
        except Exception:
            for f in new_entries:
                self._state.subscriptions.pop(f, None)
            raise
        finally:
            self._state.pending_subs.pop(pid, None)
            self._state.packet_ids.release(pid)
        return suback, {f: entry.queue for f, entry in new_entries.items()}

    async def unsubscribe(self, filters: list[str]) -> UnsubAck:
        """Send UNSUBSCRIBE, remove queues from state, return UnsubAck."""
        pid = self._state.packet_ids.acquire()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[UnsubAck] = loop.create_future()
        self._state.pending_unsubs[pid] = future
        await self._send(
            encode(
                Unsubscribe(packet_id=pid, topic_filters=tuple(filters)),
                version=self._version,
            ),
        )
        log.debug("Sent UNSUBSCRIBE", extra={"packet_id": pid})
        try:
            unsuback = await future
        finally:
            self._state.pending_unsubs.pop(pid, None)
            self._state.packet_ids.release(pid)
            for f in filters:
                self._state.subscriptions.pop(f, None)
        return unsuback

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
            case Disconnect():
                msg = "Broker sent DISCONNECT unexpectedly"
                raise MQTTProtocolError(msg)
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

    def _should_auto_ack(self, topic: str) -> bool:
        """
        Return True if the winning subscriptions all have auto_ack=True or none match.
        """
        matching = [(f, e) for f, e in self._state.subscriptions.items() if _topic_matches(e.actual_filter, topic)]
        if not matching:
            return True
        best_key = min(_filter_specificity(e.actual_filter) for _, e in matching)
        winners = [e for _, e in matching if _filter_specificity(e.actual_filter) == best_key]
        return all(e.auto_ack for e in winners)

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
        if self._should_auto_ack(packet.topic):
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
        if self._should_auto_ack(packet.topic):
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
        self._cancel_retransmit_task(flight.retransmit_task)
        self._state.packet_ids.release(packet.packet_id)
        if not flight.future.done():
            flight.future.set_result(packet)
        log.debug("QoS 1 ack received", extra={"packet_id": packet.packet_id})

    async def _handle_pubrec(self, packet: PubRec) -> None:
        flight = self._state.inflight_qos2_out.get(packet.packet_id)
        if flight is None:
            msg = f"PUBREC for unknown packet_id {packet.packet_id}"
            raise MQTTProtocolError(msg)
        if flight.state is OutboundQoS2State.PENDING_PUBCOMP:
            await self._send(self._encode(PubRel(packet_id=packet.packet_id)))
            log.debug(
                "Duplicate QoS 2 PUBREC received, resent PUBREL",
                extra={"packet_id": packet.packet_id},
            )
            return
        if flight.state is not OutboundQoS2State.PENDING_PUBREC:
            msg = f"PUBREC in wrong state {flight.state} for packet_id {packet.packet_id}"
            raise MQTTProtocolError(msg)
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
        self._cancel_retransmit_task(flight.retransmit_task)
        self._state.packet_ids.release(packet.packet_id)
        if not flight.future.done():
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

    def _cancel_retransmit_task(self, task: asyncio.Task[None] | None) -> None:
        if task is not None and not task.done():
            task.cancel()

    def _abandon_qos1_publish(self, flight: QoS1Flight) -> None:
        current = self._state.inflight_qos1.get(flight.packet_id)
        if current is not flight:
            return
        self._state.inflight_qos1.pop(flight.packet_id, None)
        self._cancel_retransmit_task(flight.retransmit_task)
        self._state.packet_ids.release(flight.packet_id)

    def _abandon_qos2_publish(self, flight: OutboundQoS2Flight) -> None:
        current = self._state.inflight_qos2_out.get(flight.packet_id)
        if current is not flight:
            return
        self._state.inflight_qos2_out.pop(flight.packet_id, None)
        self._cancel_retransmit_task(flight.retransmit_task)
        self._state.packet_ids.release(flight.packet_id)

    async def _send(self, data: bytes) -> None:
        log.debug("Sending %d bytes", len(data))
        await self._transport.write(data)

    async def _retransmit_qos1_publish(self, packet_id: int) -> None:
        try:
            while True:
                await asyncio.sleep(self._retransmit_interval)
                flight = self._state.inflight_qos1.get(packet_id)
                if flight is None or self._disconnecting:
                    return
                await self._send(self._encode(dataclasses.replace(flight.publish, dup=True)))
                log.debug(
                    "Retransmitted QoS 1 PUBLISH",
                    extra={"topic": flight.publish.topic, "packet_id": packet_id},
                )
        except asyncio.CancelledError:
            raise
        except (MQTTDisconnectedError, OSError, RuntimeError):
            log.debug(
                "Stopping QoS 1 retransmit loop",
                exc_info=True,
                extra={"packet_id": packet_id},
            )

    async def _retransmit_qos2_publish(self, packet_id: int) -> None:
        try:
            while True:
                await asyncio.sleep(self._retransmit_interval)
                flight = self._state.inflight_qos2_out.get(packet_id)
                if flight is None or self._disconnecting:
                    return
                if flight.state is OutboundQoS2State.PENDING_PUBREC:
                    await self._send(self._encode(dataclasses.replace(flight.publish, dup=True)))
                    log.debug(
                        "Retransmitted QoS 2 PUBLISH",
                        extra={"topic": flight.publish.topic, "packet_id": packet_id},
                    )
                    continue
                await self._send(self._encode(PubRel(packet_id=packet_id)))
                log.debug("Retransmitted PUBREL", extra={"packet_id": packet_id})
        except asyncio.CancelledError:
            raise
        except (MQTTDisconnectedError, OSError, RuntimeError):
            log.debug(
                "Stopping QoS 2 retransmit loop",
                exc_info=True,
                extra={"packet_id": packet_id},
            )

    async def _deliver(
        self,
        publish: Publish,
        ack_callback: Callable[[], Awaitable[None]] | None,
    ) -> None:
        snapshot = list(self._state.subscriptions.items())
        matching = [(f, e) for f, e in snapshot if _topic_matches(e.actual_filter, publish.topic)]
        if not matching:
            log.warning("No subscriber for topic %r", publish.topic)
            return
        best_key = min(_filter_specificity(e.actual_filter) for _, e in matching)
        winners = [(f, e) for f, e in matching if _filter_specificity(e.actual_filter) == best_key]
        if len(winners) > 1:
            log.warning(
                "Multiple equally-specific subscribers for %r: %s, delivering to first",
                publish.topic,
                [f for f, _ in winners],
            )
            winners = winners[:1]
        filter_, entry = winners[0]
        msg = Message(
            topic=publish.topic,
            payload=publish.payload,
            qos=publish.qos,
            retain=publish.retain,
            properties=publish.properties,
        )
        if not entry.auto_ack and ack_callback is not None:
            msg._ack_callback = ack_callback
        await entry.queue.put(msg)
        log.debug(
            "Delivered message",
            extra={"topic": publish.topic, "filter": filter_},
        )
