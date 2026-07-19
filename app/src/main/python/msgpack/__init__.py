"""
Pure-Python msgpack shim for Chaquopy (no C extension available).
ponytail: minimal impl covering pack/unpack used by ebook-converter
"""

import struct
import io


def packb(obj, **kwargs):
    buf = io.BytesIO()
    _pack(buf, obj)
    return buf.getvalue()


def unpackb(data, **kwargs):
    buf = io.BytesIO(data)
    return _unpack(buf)


pack = packb
unpack = unpackb


def _pack(buf, obj):
    if obj is None:
        buf.write(b"\xc0")
    elif isinstance(obj, bool):
        buf.write(b"\xc3" if obj else b"\xc2")
    elif isinstance(obj, int):
        if 0 <= obj <= 0x7F:
            buf.write(struct.pack("B", obj))
        elif -32 <= obj < 0:
            buf.write(struct.pack("b", obj))
        elif 0 <= obj <= 0xFF:
            buf.write(b"\xcc" + struct.pack("B", obj))
        elif 0 <= obj <= 0xFFFF:
            buf.write(b"\xcd" + struct.pack(">H", obj))
        elif 0 <= obj <= 0xFFFFFFFF:
            buf.write(b"\xce" + struct.pack(">I", obj))
        elif 0 <= obj <= 0xFFFFFFFFFFFFFFFF:
            buf.write(b"\xcf" + struct.pack(">Q", obj))
        elif -128 <= obj < 0:
            buf.write(b"\xd0" + struct.pack("b", obj))
        elif -32768 <= obj < 0:
            buf.write(b"\xd1" + struct.pack(">h", obj))
        elif -2147483648 <= obj < 0:
            buf.write(b"\xd2" + struct.pack(">i", obj))
        else:
            buf.write(b"\xd3" + struct.pack(">q", obj))
    elif isinstance(obj, float):
        buf.write(b"\xcb" + struct.pack(">d", obj))
    elif isinstance(obj, bytes):
        n = len(obj)
        if n <= 0xFF:
            buf.write(b"\xc4" + struct.pack("B", n))
        elif n <= 0xFFFF:
            buf.write(b"\xc5" + struct.pack(">H", n))
        else:
            buf.write(b"\xc6" + struct.pack(">I", n))
        buf.write(obj)
    elif isinstance(obj, str):
        raw = obj.encode("utf-8")
        n = len(raw)
        if n <= 31:
            buf.write(struct.pack("B", 0xA0 | n))
        elif n <= 0xFF:
            buf.write(b"\xd9" + struct.pack("B", n))
        elif n <= 0xFFFF:
            buf.write(b"\xda" + struct.pack(">H", n))
        else:
            buf.write(b"\xdb" + struct.pack(">I", n))
        buf.write(raw)
    elif isinstance(obj, (list, tuple)):
        n = len(obj)
        if n <= 15:
            buf.write(struct.pack("B", 0x90 | n))
        elif n <= 0xFFFF:
            buf.write(b"\xdc" + struct.pack(">H", n))
        else:
            buf.write(b"\xdd" + struct.pack(">I", n))
        for item in obj:
            _pack(buf, item)
    elif isinstance(obj, dict):
        n = len(obj)
        if n <= 15:
            buf.write(struct.pack("B", 0x80 | n))
        elif n <= 0xFFFF:
            buf.write(b"\xde" + struct.pack(">H", n))
        else:
            buf.write(b"\xdf" + struct.pack(">I", n))
        for k, v in obj.items():
            _pack(buf, k)
            _pack(buf, v)
    else:
        raise TypeError(f"Cannot serialize {type(obj)}")


def _unpack(buf):
    b = buf.read(1)
    if not b:
        raise ValueError("Unexpected end of data")
    c = b[0]

    if c <= 0x7F:
        return c
    elif c >= 0xE0:
        return c - 256
    elif c & 0xE0 == 0xA0:
        n = c & 0x1F
        return buf.read(n).decode("utf-8")
    elif c & 0xF0 == 0x90:
        n = c & 0x0F
        return [_unpack(buf) for _ in range(n)]
    elif c & 0xF0 == 0x80:
        n = c & 0x0F
        return {_unpack(buf): _unpack(buf) for _ in range(n)}
    elif c == 0xC0:
        return None
    elif c == 0xC2:
        return False
    elif c == 0xC3:
        return True
    elif c == 0xC4:
        n = struct.unpack("B", buf.read(1))[0]
        return buf.read(n)
    elif c == 0xC5:
        n = struct.unpack(">H", buf.read(2))[0]
        return buf.read(n)
    elif c == 0xC6:
        n = struct.unpack(">I", buf.read(4))[0]
        return buf.read(n)
    elif c == 0xCA:
        return struct.unpack(">f", buf.read(4))[0]
    elif c == 0xCB:
        return struct.unpack(">d", buf.read(8))[0]
    elif c == 0xCC:
        return struct.unpack("B", buf.read(1))[0]
    elif c == 0xCD:
        return struct.unpack(">H", buf.read(2))[0]
    elif c == 0xCE:
        return struct.unpack(">I", buf.read(4))[0]
    elif c == 0xCF:
        return struct.unpack(">Q", buf.read(8))[0]
    elif c == 0xD0:
        return struct.unpack("b", buf.read(1))[0]
    elif c == 0xD1:
        return struct.unpack(">h", buf.read(2))[0]
    elif c == 0xD2:
        return struct.unpack(">i", buf.read(4))[0]
    elif c == 0xD3:
        return struct.unpack(">q", buf.read(8))[0]
    elif c == 0xD9:
        n = struct.unpack("B", buf.read(1))[0]
        return buf.read(n).decode("utf-8")
    elif c == 0xDA:
        n = struct.unpack(">H", buf.read(2))[0]
        return buf.read(n).decode("utf-8")
    elif c == 0xDB:
        n = struct.unpack(">I", buf.read(4))[0]
        return buf.read(n).decode("utf-8")
    elif c == 0xDC:
        n = struct.unpack(">H", buf.read(2))[0]
        return [_unpack(buf) for _ in range(n)]
    elif c == 0xDD:
        n = struct.unpack(">I", buf.read(4))[0]
        return [_unpack(buf) for _ in range(n)]
    elif c == 0xDE:
        n = struct.unpack(">H", buf.read(2))[0]
        return {_unpack(buf): _unpack(buf) for _ in range(n)}
    elif c == 0xDF:
        n = struct.unpack(">I", buf.read(4))[0]
        return {_unpack(buf): _unpack(buf) for _ in range(n)}
    else:
        raise ValueError(f"Unknown msgpack type: 0x{c:02x}")


# Compatibility aliases
dumps = packb
loads = unpackb
