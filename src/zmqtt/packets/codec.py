"""I/O-free encode/decode dispatcher for MQTT 3.1.1 and 5.0 packets."""

import struct
from typing import Literal

from zmqtt.packets._wire import (
    decode_bytes_field,
    decode_str,
    decode_varint,
    encode_bytes_field,
    encode_str,
    encode_varint,
)
from zmqtt.packets.auth import Auth
from zmqtt.packets.connect import ConnAck, Connect, Will
from zmqtt.packets.disconnect import Disconnect
from zmqtt.packets.ping import PingReq, PingResp
from zmqtt.packets.properties import (
    decode_auth_properties,
    decode_connack_properties,
    decode_connect_properties,
    decode_disconnect_properties,
    decode_puback_properties,
    decode_publish_properties,
    decode_suback_properties,
    decode_subscribe_properties,
    decode_unsuback_properties,
    decode_unsubscribe_properties,
    decode_will_properties,
    encode_auth_properties,
    encode_connack_properties,
    encode_connect_properties,
    encode_disconnect_properties,
    encode_puback_properties,
    encode_publish_properties,
    encode_suback_properties,
    encode_subscribe_properties,
    encode_unsuback_properties,
    encode_unsubscribe_properties,
    encode_will_properties,
)
from zmqtt.packets.publish import PubAck, PubComp, Publish, PubRec, PubRel
from zmqtt.packets.subscribe import (
    SubAck,
    Subscribe,
    SubscriptionRequest,
    UnsubAck,
    Unsubscribe,
)
from zmqtt.packets.types import PacketType
from zmqtt.types import QoS, RetainHandling

# Re-export wire helpers so existing callers of ``from zmqtt.packets.codec import …`` keep working.
__all__ = [
    "decode",
    "decode_varint",
    "encode",
    "encode_varint",
]

AnyPacket = (
    Connect
    | ConnAck
    | Publish
    | PubAck
    | PubRec
    | PubRel
    | PubComp
    | Subscribe
    | SubAck
    | Unsubscribe
    | UnsubAck
    | PingReq
    | PingResp
    | Disconnect
    | Auth
)


def _build_packet(packet_type: PacketType, flags: int, body: bytes) -> bytes:
    first_byte = (packet_type << 4) | (flags & 0x0F)
    return bytes([first_byte]) + encode_varint(len(body)) + body


def _packet_id_only(packet_type: PacketType, flags: int, packet_id: int) -> bytes:
    return _build_packet(packet_type, flags, struct.pack("!H", packet_id))


def _encode_connect(packet: Connect, version: Literal["3.1.1", "5.0"]) -> bytes:
    flags = 0
    if packet.clean_session:
        flags |= 0x02
    if packet.will is not None:
        flags |= 0x04
        flags |= packet.will.qos << 3
        if packet.will.retain:
            flags |= 0x20
    if packet.username is not None:
        flags |= 0x80
    if packet.password is not None:
        flags |= 0x40

    proto_level = 0x05 if version == "5.0" else 0x04
    var_header = encode_str("MQTT") + bytes([proto_level, flags]) + struct.pack("!H", packet.keepalive)

    if version == "5.0":
        var_header += encode_connect_properties(packet.properties)

    payload = encode_str(packet.client_id)
    if packet.will is not None:
        if version == "5.0":
            payload += encode_will_properties(packet.will.properties)
        payload += encode_str(packet.will.topic)
        payload += encode_bytes_field(packet.will.payload)
    if packet.username is not None:
        payload += encode_str(packet.username)
    if packet.password is not None:
        payload += encode_bytes_field(packet.password)

    return _build_packet(PacketType.CONNECT, 0, var_header + payload)


def _encode_connack(packet: ConnAck, version: Literal["3.1.1", "5.0"]) -> bytes:
    sp = 0x01 if packet.session_present else 0x00
    body = bytes([sp, packet.return_code])
    if version == "5.0":
        body += encode_connack_properties(packet.properties)
    return _build_packet(PacketType.CONNACK, 0, body)


def _encode_publish(packet: Publish, version: Literal["3.1.1", "5.0"]) -> bytes:
    flags = (packet.dup << 3) | (packet.qos << 1) | packet.retain
    body = encode_str(packet.topic)
    if packet.qos != QoS.AT_MOST_ONCE:
        if packet.packet_id is None:
            msg = "Cannot publish without packet id"
            raise ValueError(msg)
        body += struct.pack("!H", packet.packet_id)
    if version == "5.0":
        body += encode_publish_properties(packet.properties)
    body += packet.payload
    return _build_packet(PacketType.PUBLISH, flags, body)


def _encode_pub_ack_variant(
    packet_type: PacketType,
    flags: int,
    packet_id: int,
    reason_code: int,
    properties: object,
    version: Literal["3.1.1", "5.0"],
) -> bytes:
    if version == "5.0" and (reason_code != 0 or properties is not None):
        body = struct.pack("!HB", packet_id, reason_code)
        body += encode_puback_properties(properties)  # type: ignore[arg-type]
        return _build_packet(packet_type, flags, body)
    return _packet_id_only(packet_type, flags, packet_id)


def _encode_subscribe(packet: Subscribe, version: Literal["3.1.1", "5.0"]) -> bytes:
    body = struct.pack("!H", packet.packet_id)
    if version == "5.0":
        body += encode_subscribe_properties(packet.properties)
    for sub in packet.subscriptions:
        body += encode_str(sub.topic_filter)
        if version == "5.0":
            options = sub.qos | (sub.no_local << 2) | (sub.retain_as_published << 3) | (sub.retain_handling << 4)
        else:
            options = sub.qos
        body += bytes([options])
    return _build_packet(PacketType.SUBSCRIBE, 0b0010, body)


def _encode_suback(packet: SubAck, version: Literal["3.1.1", "5.0"]) -> bytes:
    body = struct.pack("!H", packet.packet_id)
    if version == "5.0":
        body += encode_suback_properties(packet.properties)
    body += bytes(packet.return_codes)
    return _build_packet(PacketType.SUBACK, 0, body)


def _encode_unsubscribe(packet: Unsubscribe, version: Literal["3.1.1", "5.0"]) -> bytes:
    body = struct.pack("!H", packet.packet_id)
    if version == "5.0":
        body += encode_unsubscribe_properties(packet.properties)
    for topic in packet.topic_filters:
        body += encode_str(topic)
    return _build_packet(PacketType.UNSUBSCRIBE, 0b0010, body)


def _encode_unsuback(packet: UnsubAck, version: Literal["3.1.1", "5.0"]) -> bytes:
    if version == "5.0":
        body = struct.pack("!H", packet.packet_id)
        body += encode_unsuback_properties(packet.properties)
        body += bytes(packet.reason_codes)
        return _build_packet(PacketType.UNSUBACK, 0, body)
    return _packet_id_only(PacketType.UNSUBACK, 0, packet.packet_id)


def _encode_disconnect(packet: Disconnect, version: Literal["3.1.1", "5.0"]) -> bytes:
    if version == "5.0" and (packet.reason_code != 0 or packet.properties is not None):
        body = bytes([packet.reason_code]) + encode_disconnect_properties(
            packet.properties,
        )
        return _build_packet(PacketType.DISCONNECT, 0, body)
    return _build_packet(PacketType.DISCONNECT, 0, b"")


def encode(packet: AnyPacket, *, version: Literal["3.1.1", "5.0"]) -> bytes:
    match packet:
        case Connect():
            return _encode_connect(packet, version)
        case ConnAck():
            return _encode_connack(packet, version)
        case Publish():
            return _encode_publish(packet, version)
        case PubAck(packet_id=pid, reason_code=rc, properties=props):
            return _encode_pub_ack_variant(
                PacketType.PUBACK,
                0,
                pid,
                rc,
                props,
                version,
            )
        case PubRec(packet_id=pid, reason_code=rc, properties=props):
            return _encode_pub_ack_variant(
                PacketType.PUBREC,
                0,
                pid,
                rc,
                props,
                version,
            )
        case PubRel(packet_id=pid, reason_code=rc, properties=props):
            return _encode_pub_ack_variant(
                PacketType.PUBREL,
                0b0010,
                pid,
                rc,
                props,
                version,
            )
        case PubComp(packet_id=pid, reason_code=rc, properties=props):
            return _encode_pub_ack_variant(
                PacketType.PUBCOMP,
                0,
                pid,
                rc,
                props,
                version,
            )
        case Subscribe():
            return _encode_subscribe(packet, version)
        case SubAck():
            return _encode_suback(packet, version)
        case Unsubscribe():
            return _encode_unsubscribe(packet, version)
        case UnsubAck():
            return _encode_unsuback(packet, version)
        case PingReq():
            return _build_packet(PacketType.PINGREQ, 0, b"")
        case PingResp():
            return _build_packet(PacketType.PINGRESP, 0, b"")
        case Disconnect():
            return _encode_disconnect(packet, version)
        case Auth():
            body = bytes([packet.reason_code]) + encode_auth_properties(
                packet.properties,
            )
            return _build_packet(PacketType.AUTH, 0, body)
        case _:
            msg = f"Unknown packet type: {type(packet)}"
            raise ValueError(msg)


def _decode_connect(buf: bytes | memoryview) -> Connect:
    proto_name, pos = decode_str(buf, 0)
    if proto_name != "MQTT":
        msg = f"Invalid protocol name: {proto_name!r}"
        raise ValueError(msg)
    proto_level = buf[pos]
    if proto_level not in (0x04, 0x05):
        msg = f"Unsupported protocol level: {proto_level}"
        raise ValueError(msg)
    ver: Literal["3.1.1", "5.0"] = "5.0" if proto_level == 0x05 else "3.1.1"
    pos += 1

    flags = buf[pos]
    pos += 1
    clean_session = bool(flags & 0x02)
    will_flag = bool(flags & 0x04)
    will_qos = QoS((flags >> 3) & 0x03)
    will_retain = bool(flags & 0x20)
    username_flag = bool(flags & 0x80)
    password_flag = bool(flags & 0x40)

    (keepalive,) = struct.unpack_from("!H", buf, pos)
    pos += 2

    connect_props = None
    if ver == "5.0":
        connect_props, n = decode_connect_properties(buf, pos)
        pos += n

    client_id, n = decode_str(buf, pos)
    pos += n

    will = None
    if will_flag:
        will_props = None
        if ver == "5.0":
            will_props, n = decode_will_properties(buf, pos)
            pos += n
        topic, n = decode_str(buf, pos)
        pos += n
        payload, n = decode_bytes_field(buf, pos)
        pos += n
        will = Will(
            topic=topic,
            payload=payload,
            qos=will_qos,
            retain=will_retain,
            properties=will_props,
        )

    username = None
    if username_flag:
        username, n = decode_str(buf, pos)
        pos += n

    password = None
    if password_flag:
        password, n = decode_bytes_field(buf, pos)
        pos += n

    return Connect(
        client_id=client_id,
        clean_session=clean_session,
        keepalive=keepalive,
        username=username,
        password=password,
        will=will,
        properties=connect_props,
    )


def _decode_connack(
    buf: bytes | memoryview,
    version: Literal["3.1.1", "5.0"],
) -> ConnAck:
    if len(buf) < 2:
        msg = "CONNACK too short"
        raise ValueError(msg)
    session_present = bool(buf[0] & 0x01)
    return_code = buf[1]
    props = None
    if version == "5.0" and len(buf) > 2:
        props, _ = decode_connack_properties(buf, 2)
    return ConnAck(
        session_present=session_present,
        return_code=return_code,
        properties=props,
    )


def _decode_publish(
    buf: bytes | memoryview,
    flags: int,
    version: Literal["3.1.1", "5.0"],
) -> Publish:
    dup = bool(flags & 0x08)
    qos = QoS((flags >> 1) & 0x03)
    retain = bool(flags & 0x01)

    topic, pos = decode_str(buf, 0)

    packet_id = None
    if qos != QoS.AT_MOST_ONCE:
        if pos + 2 > len(buf):
            msg = "PUBLISH too short for packet ID"
            raise ValueError(msg)
        (packet_id,) = struct.unpack_from("!H", buf, pos)
        pos += 2

    props = None
    if version == "5.0":
        props, n = decode_publish_properties(buf, pos)
        pos += n

    return Publish(
        topic=topic,
        payload=bytes(buf[pos:]),
        qos=qos,
        retain=retain,
        dup=dup,
        packet_id=packet_id,
        properties=props,
    )


def _decode_pub_ack_variant(
    buf: bytes | memoryview,
    version: Literal["3.1.1", "5.0"],
) -> tuple[int, int, object]:
    """Returns (packet_id, reason_code, properties)."""
    if len(buf) < 2:
        msg = "Packet too short for packet ID"
        raise ValueError(msg)
    (packet_id,) = struct.unpack_from("!H", buf, 0)
    reason_code = 0
    props = None
    if version == "5.0" and len(buf) > 2:
        reason_code = buf[2]
        if len(buf) > 3:
            props, _ = decode_puback_properties(buf, 3)
    return packet_id, reason_code, props


def _decode_subscribe(
    buf: bytes | memoryview,
    version: Literal["3.1.1", "5.0"],
) -> Subscribe:
    if len(buf) < 2:
        msg = "SUBSCRIBE too short"
        raise ValueError(msg)
    (packet_id,) = struct.unpack_from("!H", buf, 0)
    pos = 2

    props = None
    if version == "5.0":
        props, n = decode_subscribe_properties(buf, pos)
        pos += n

    subscriptions: list[SubscriptionRequest] = []
    while pos < len(buf):
        topic_filter, n = decode_str(buf, pos)
        pos += n
        if pos >= len(buf):
            msg = "SUBSCRIBE missing options byte"
            raise ValueError(msg)
        options = buf[pos]
        pos += 1
        qos = QoS(options & 0x03)
        if version == "5.0":
            no_local = bool(options & 0x04)
            retain_as_published = bool(options & 0x08)
            retain_handling = RetainHandling((options >> 4) & 0x03)
            subscriptions.append(
                SubscriptionRequest(
                    topic_filter=topic_filter,
                    qos=qos,
                    no_local=no_local,
                    retain_as_published=retain_as_published,
                    retain_handling=retain_handling,
                ),
            )
        else:
            subscriptions.append(
                SubscriptionRequest(topic_filter=topic_filter, qos=qos),
            )
    return Subscribe(
        packet_id=packet_id,
        subscriptions=tuple(subscriptions),
        properties=props,
    )


def _decode_suback(buf: bytes | memoryview, version: Literal["3.1.1", "5.0"]) -> SubAck:
    if len(buf) < 2:
        msg = "SUBACK too short"
        raise ValueError(msg)
    (packet_id,) = struct.unpack_from("!H", buf, 0)
    pos = 2
    props = None
    if version == "5.0":
        props, n = decode_suback_properties(buf, pos)
        pos += n
    return SubAck(packet_id=packet_id, return_codes=tuple(buf[pos:]), properties=props)


def _decode_unsubscribe(
    buf: bytes | memoryview,
    version: Literal["3.1.1", "5.0"],
) -> Unsubscribe:
    if len(buf) < 2:
        msg = "UNSUBSCRIBE too short"
        raise ValueError(msg)
    (packet_id,) = struct.unpack_from("!H", buf, 0)
    pos = 2
    props = None
    if version == "5.0":
        props, n = decode_unsubscribe_properties(buf, pos)
        pos += n
    topic_filters: list[str] = []
    while pos < len(buf):
        topic_filter, n = decode_str(buf, pos)
        pos += n
        topic_filters.append(topic_filter)
    return Unsubscribe(
        packet_id=packet_id,
        topic_filters=tuple(topic_filters),
        properties=props,
    )


def _decode_unsuback(
    buf: bytes | memoryview,
    version: Literal["3.1.1", "5.0"],
) -> UnsubAck:
    if len(buf) < 2:
        msg = "UNSUBACK too short"
        raise ValueError(msg)
    (packet_id,) = struct.unpack_from("!H", buf, 0)
    if version == "5.0":
        pos = 2
        props, n = decode_unsuback_properties(buf, pos)
        pos += n
        return UnsubAck(
            packet_id=packet_id,
            reason_codes=tuple(buf[pos:]),
            properties=props,
        )
    return UnsubAck(packet_id=packet_id)


def _decode_disconnect(
    buf: bytes | memoryview,
    version: Literal["3.1.1", "5.0"],
) -> Disconnect:
    reason_code = 0
    props = None
    if version == "5.0" and len(buf) >= 1:
        reason_code = buf[0]
        if len(buf) > 1:
            props, _ = decode_disconnect_properties(buf, 1)
    return Disconnect(reason_code=reason_code, properties=props)


def decode(
    buffer: bytes | memoryview,
    *,
    version: Literal["3.1.1", "5.0"] = "3.1.1",
) -> tuple[AnyPacket, int] | None:
    """Decode one packet from buffer.

    Returns (packet, bytes_consumed) or None if the buffer holds fewer bytes
    than the next complete packet requires.  Pass version="5.0" when operating
    in an MQTT 5.0 session (CONNECT auto-detects its own version regardless).
    """
    if len(buffer) < 2:
        return None

    first_byte = buffer[0]
    packet_type_val = (first_byte >> 4) & 0x0F
    flags = first_byte & 0x0F

    # Inline varint decode so we can return None on incomplete data.
    remaining_length = 0
    multiplier = 1
    varint_bytes = 0
    for i in range(4):
        if 1 + i >= len(buffer):
            return None  # fixed header not fully received
        byte = buffer[1 + i]
        remaining_length += (byte & 0x7F) * multiplier
        varint_bytes += 1
        if byte & 0x80 == 0:
            break
        multiplier *= 128
    else:
        msg = "Remaining-length varint exceeds 4 bytes"
        raise ValueError(msg)

    total = 1 + varint_bytes + remaining_length
    if len(buffer) < total:
        return None  # packet body not yet fully received

    try:
        packet_type = PacketType(packet_type_val)
    except ValueError as e:
        msg = f"Unknown MQTT packet type: {packet_type_val}"
        raise ValueError(msg) from e

    body = buffer[1 + varint_bytes : total]

    match packet_type:
        case PacketType.CONNECT:
            packet: AnyPacket = _decode_connect(body)
        case PacketType.CONNACK:
            packet = _decode_connack(body, version)
        case PacketType.PUBLISH:
            packet = _decode_publish(body, flags, version)
        case PacketType.PUBACK:
            pid, rc, props = _decode_pub_ack_variant(body, version)
            packet = PubAck(packet_id=pid, reason_code=rc, properties=props)  # type: ignore[arg-type]
        case PacketType.PUBREC:
            pid, rc, props = _decode_pub_ack_variant(body, version)
            packet = PubRec(packet_id=pid, reason_code=rc, properties=props)  # type: ignore[arg-type]
        case PacketType.PUBREL:
            pid, rc, props = _decode_pub_ack_variant(body, version)
            packet = PubRel(packet_id=pid, reason_code=rc, properties=props)  # type: ignore[arg-type]
        case PacketType.PUBCOMP:
            pid, rc, props = _decode_pub_ack_variant(body, version)
            packet = PubComp(packet_id=pid, reason_code=rc, properties=props)  # type: ignore[arg-type]
        case PacketType.SUBSCRIBE:
            packet = _decode_subscribe(body, version)
        case PacketType.SUBACK:
            packet = _decode_suback(body, version)
        case PacketType.UNSUBSCRIBE:
            packet = _decode_unsubscribe(body, version)
        case PacketType.UNSUBACK:
            packet = _decode_unsuback(body, version)
        case PacketType.PINGREQ:
            packet = PingReq()
        case PacketType.PINGRESP:
            packet = PingResp()
        case PacketType.DISCONNECT:
            packet = _decode_disconnect(body, version)
        case PacketType.AUTH:
            if len(body) < 1:
                msg = "AUTH too short"
                raise ValueError(msg)
            auth_rc = body[0]
            auth_props = None
            if len(body) > 1:
                auth_props, _ = decode_auth_properties(body, 1)
            packet = Auth(reason_code=auth_rc, properties=auth_props)
        case _:
            msg = f"Unsupported packet type: {packet_type}"
            raise ValueError(msg)

    return packet, total
