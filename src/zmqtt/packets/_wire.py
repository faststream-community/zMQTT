"""Internal wire-format helpers: variable-length integers, UTF-8 strings, binary data."""

import struct
from typing import Final

_MAX_MQTT_VARINT: Final = 268_435_455


def encode_varint(value: int) -> bytes:
    """Encode a non-negative integer as MQTT variable-length integer (1-4 bytes)."""
    if value < 0 or value > _MAX_MQTT_VARINT:
        msg = f"Value {value} out of varint range"
        raise ValueError(msg)
    result = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            byte |= 0x80
        result.append(byte)
        if not value:
            break
    return bytes(result)


def decode_varint(buf: bytes | memoryview, offset: int = 0) -> tuple[int, int]:
    """Decode MQTT variable-length integer.

    Returns (value, bytes_consumed). Raises ValueError if buffer is incomplete
    or the encoding exceeds 4 bytes.
    """
    multiplier = 1
    value = 0
    for i in range(4):
        pos = offset + i
        if pos >= len(buf):
            msg = "Buffer too short for varint"
            raise ValueError(msg)
        byte = buf[pos]
        value += (byte & 0x7F) * multiplier
        if byte & 0x80 == 0:
            return value, i + 1
        multiplier *= 128
    msg = "Varint exceeds 4 bytes"
    raise ValueError(msg)


def encode_str(s: str) -> bytes:
    encoded = s.encode("utf-8")
    return struct.pack("!H", len(encoded)) + encoded


def decode_str(buf: bytes | memoryview, offset: int) -> tuple[str, int]:
    if offset + 2 > len(buf):
        msg = "Buffer too short for string length prefix"
        raise ValueError(msg)
    (length,) = struct.unpack_from("!H", buf, offset)
    end = offset + 2 + length
    if end > len(buf):
        msg = "Buffer too short for string data"
        raise ValueError(msg)
    return bytes(buf[offset + 2 : end]).decode("utf-8"), 2 + length


def encode_bytes_field(data: bytes) -> bytes:
    return struct.pack("!H", len(data)) + data


def decode_bytes_field(buf: bytes | memoryview, offset: int) -> tuple[bytes, int]:
    if offset + 2 > len(buf):
        msg = "Buffer too short for bytes length prefix"
        raise ValueError(msg)
    (length,) = struct.unpack_from("!H", buf, offset)
    end = offset + 2 + length
    if end > len(buf):
        msg = "Buffer too short for bytes data"
        raise ValueError(msg)
    return bytes(buf[offset + 2 : end]), 2 + length
