"""Protocol engine: ties codec, transport, and session state together."""

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable
from typing import Final, Literal

from zmqtt.errors import (
    MQTTConnectError,
    MQTTDisconnectedError,
    MQTTProtocolError,
    MQTTTimeoutError,
)
from zmqtt.log import get_logger
from zmqtt.packets.auth import Auth
from zmqtt.packets.codec import AnyPacket, encode
from zmqtt.packets.connect import ConnAck, Connect
from zmqtt.packets.disconnect import Disconnect
from zmqtt.packets.ping import PingReq, PingResp
from zmqtt.packets.publish import PubAck, PubComp, Publish, PubRec, PubRel
from zmqtt.packets.reader import PacketBuffer
from zmqtt.packets.subscribe import (
    SubAck,
    Subscribe,
    SubscriptionRequest,
    UnsubAck,
    Unsubscribe,
)
from zmqtt.state import (
    InboundQoS2Flight,
    InboundQoS2State,
    OutboundQoS2Flight,
    OutboundQoS2State,
    QoS1Flight,
    SessionState,
    SubscriptionEntry,
)
from zmqtt.transport.base import Transport
from zmqtt.types import Message, QoS

log = get_logger(__name__)


def _topic_matches(filter_: str, topic: str) -> bool:
    """Return True if topic matches the MQTT topic filter."""
    # $-prefixed topics are not matched by wildcards unless filter also starts with $
    if topic.startswith("$") and not filter_.startswith("$"):
        return False
    return _match_parts(filter_.split("/"), topic.split("/"))


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


def _filter_specificity(filter_: str) -> tuple[int, ...]:
    """Return a sort key for a filter; lexicographically smaller == more specific."""
    return tuple(_segment_rank(s) for s in filter_.split("/"))


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
        version: Literal["3.1.1", "5.0"] = "3.1.1",
    ) -> None:
        self._transport = transport
        self._state = state
        self._keepalive = keepalive
        self._ping_timeout = ping_timeout
        self._version: Final = version
        self._buf = PacketBuffer(version=version)
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
                    raise MQTTProtocolError(f"Expected CONNACK, got {pkt!r}")
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
        try:
            await self._send(self._encode(Disconnect()))
        except Exception:
            pass
        await self._transport.close()
        log.info("Disconnected")

    def _cancel_pending(self) -> None:
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

    async def publish(self, packet: Publish) -> PubAck | PubComp | None:
        """Publish a message. Returns PubAck (QoS 1), PubComp (QoS 2), or None (QoS 0)."""
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
                    packet_id=pid, publish=packet, future=future
                )
                await self._send(self._encode(packet))
                log.debug(
                    "Published QoS 1", extra={"topic": packet.topic, "packet_id": pid}
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
                    "Published QoS 2", extra={"topic": packet.topic, "packet_id": pid}
                )
                return await future2

    async def subscribe(
        self, filters: list[SubscriptionRequest], *, auto_ack: bool = True
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
                    queue=asyncio.Queue(), auto_ack=auto_ack
                )
        self._state.subscriptions.update(new_entries)
        future: asyncio.Future[SubAck] = loop.create_future()
        self._state.pending_subs[pid] = future
        await self._send(
            self._encode(Subscribe(packet_id=pid, subscriptions=tuple(filters)))
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
            )
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
        except asyncio.TimeoutError:
            self._ping_waiters.remove(future)
            raise MQTTTimeoutError("PINGRESP not received within timeout")
        return loop.time() - t0

    async def send_auth(self, packet: Auth) -> None:
        """Send an AUTH packet (MQTT 5.0 enhanced authentication)."""
        if self._version != "5.0":
            raise RuntimeError(
                f"Feature is not supported for mqtt protocol version {self._version}"
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

    async def _dispatch(self, packet: AnyPacket) -> None:
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
                print(f"SUBACK: {packet}")
                await self._handle_suback(packet)
            case UnsubAck():
                print(f"UnsubAck: {packet}")
                await self._handle_unsuback(packet)
            case PingResp():
                self._handle_pingresp()
            case Disconnect():
                raise MQTTProtocolError("Broker sent DISCONNECT unexpectedly")
            case Auth():
                if self._version != "5.0":
                    raise MQTTProtocolError(
                        "Received AUTH packet in MQTT 3.1.1 session"
                    )
                # AUTH exchange is handled by the caller via auth(); ignore here.
            case _:
                raise MQTTProtocolError(f"Unexpected packet from broker: {packet!r}")

    def _should_auto_ack(self, topic: str) -> bool:
        """Return True if the winning subscription(s) all have auto_ack=True (or none match)."""
        matching = [
            (f, e)
            for f, e in self._state.subscriptions.items()
            if _topic_matches(f, topic)
        ]
        if not matching:
            return True
        best_key = min(_filter_specificity(f) for f, _ in matching)
        winners = [e for f, e in matching if _filter_specificity(f) == best_key]
        return all(e.auto_ack for e in winners)

    async def _handle_publish(self, packet: Publish) -> None:
        match packet.qos:
            case QoS.AT_MOST_ONCE:
                await self._deliver(packet, ack_callback=None)

            case QoS.AT_LEAST_ONCE:
                assert packet.packet_id is not None
                if self._should_auto_ack(packet.topic):
                    await self._send(self._encode(PubAck(packet_id=packet.packet_id)))
                    await self._deliver(packet, ack_callback=None)
                else:
                    pid = packet.packet_id
                    acked = False

                    async def _puback() -> None:
                        nonlocal acked
                        if acked:
                            return
                        acked = True
                        await self._send(self._encode(PubAck(packet_id=pid)))

                    await self._deliver(packet, ack_callback=_puback)

            case QoS.EXACTLY_ONCE:
                assert packet.packet_id is not None
                pid = packet.packet_id
                if pid in self._state.inflight_qos2_in:
                    # PUBREC already sent — resend it (duplicate PUBLISH after PUBREC)
                    await self._send(self._encode(PubRec(packet_id=pid)))
                    return
                if pid in self._state.pending_ack_qos2_in:
                    # Duplicate PUBLISH while app hasn't called msg.ack() yet — ignore
                    return
                if self._should_auto_ack(packet.topic):
                    self._state.inflight_qos2_in[pid] = InboundQoS2Flight(
                        packet_id=pid,
                        publish=packet,
                        state=InboundQoS2State.PENDING_PUBREL,
                    )
                    await self._send(self._encode(PubRec(packet_id=pid)))
                else:
                    self._state.pending_ack_qos2_in.add(pid)

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
            raise MQTTProtocolError(f"PUBREL for unknown packet_id {packet.packet_id}")
        await self._send(self._encode(PubComp(packet_id=packet.packet_id)))
        await self._deliver(flight.publish, ack_callback=None)

    async def _handle_puback(self, packet: PubAck) -> None:
        flight = self._state.inflight_qos1.pop(packet.packet_id, None)
        if flight is None:
            raise MQTTProtocolError(f"PUBACK for unknown packet_id {packet.packet_id}")
        self._state.packet_ids.release(packet.packet_id)
        flight.future.set_result(packet)
        log.debug("QoS 1 ack received", extra={"packet_id": packet.packet_id})

    async def _handle_pubrec(self, packet: PubRec) -> None:
        flight = self._state.inflight_qos2_out.get(packet.packet_id)
        if flight is None:
            raise MQTTProtocolError(f"PUBREC for unknown packet_id {packet.packet_id}")
        if flight.state is not OutboundQoS2State.PENDING_PUBREC:
            raise MQTTProtocolError(
                f"PUBREC in wrong state {flight.state} for packet_id {packet.packet_id}"
            )
        flight.state = OutboundQoS2State.PENDING_PUBCOMP
        await self._send(self._encode(PubRel(packet_id=packet.packet_id)))
        log.debug(
            "QoS 2 PUBREC received, sent PUBREL", extra={"packet_id": packet.packet_id}
        )

    async def _handle_pubcomp(self, packet: PubComp) -> None:
        flight = self._state.inflight_qos2_out.pop(packet.packet_id, None)
        if flight is None:
            raise MQTTProtocolError(f"PUBCOMP for unknown packet_id {packet.packet_id}")
        self._state.packet_ids.release(packet.packet_id)
        flight.future.set_result(packet)
        log.debug("QoS 2 complete", extra={"packet_id": packet.packet_id})

    async def _handle_suback(self, packet: SubAck) -> None:
        future = self._state.pending_subs.get(packet.packet_id)
        if future is None:
            raise MQTTProtocolError(f"SUBACK for unknown packet_id {packet.packet_id}")
        future.set_result(packet)

    async def _handle_unsuback(self, packet: UnsubAck) -> None:
        future = self._state.pending_unsubs.get(packet.packet_id)
        if future is None:
            raise MQTTProtocolError(
                f"UNSUBACK for unknown packet_id {packet.packet_id}"
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
        self, publish: Publish, ack_callback: Callable[[], Awaitable[None]] | None
    ) -> None:
        snapshot = list(self._state.subscriptions.items())
        matching = [(f, e) for f, e in snapshot if _topic_matches(f, publish.topic)]
        if not matching:
            log.warning("No subscriber for topic %r", publish.topic)
            return
        best_key = min(_filter_specificity(f) for f, _ in matching)
        winners = [(f, e) for f, e in matching if _filter_specificity(f) == best_key]
        if len(winners) > 1:
            log.warning(
                "Multiple equally-specific subscribers for %r: %s — delivering to first",
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
            "Delivered message", extra={"topic": publish.topic, "filter": filter_}
        )
