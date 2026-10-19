"""Bounded CryEngine CXmlArchive reader/writer used by KCD2 editor scenes.

The layout is documented by CryEngine's CXmlArchive/CNamedData serializers.
Unknown flags and wide strings fail instead of being silently discarded.
"""
import struct
import xml.etree.ElementTree as ET
import zlib


def encode_string(value):
    value = value.encode('utf-8') if isinstance(value, str) else value
    size = len(value)
    prefix = (bytes([size]) if size < 255 else
              b'\xff' + struct.pack('<H', size) if size < 65534 else
              b'\xff\xff\xff' + struct.pack('<I', size))
    return prefix + value


def decode(blob, max_block_size=512 * 1024 * 1024):
    cursor = 0
    def take(size):
        nonlocal cursor
        if size < 0 or cursor + size > len(blob):
            raise ValueError('Truncated editor archive')
        result = blob[cursor:cursor + size]
        cursor += size
        return result
    def string():
        size = take(1)[0]
        if size == 255:
            size, = struct.unpack('<H', take(2))
            if size == 65534:
                raise ValueError('Unsupported wide editor string')
            if size == 65535:
                size, = struct.unpack('<I', take(4))
        return take(size)
    root = ET.fromstring(string())
    count, = struct.unpack('<I', take(4))
    if count > 10000:
        raise ValueError('Invalid editor block count')
    blocks = {}
    for _ in range(count):
        name = string().decode('utf-8')
        size_flags, original_size, flags = struct.unpack('<III', take(12))
        if flags or original_size > max_block_size or name in blocks:
            raise ValueError('Invalid editor block metadata')
        data = take(size_flags & 0x7fffffff)
        if size_flags >> 31:
            decoder = zlib.decompressobj()
            data = decoder.decompress(data, original_size + 1)
            if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                raise ValueError('Invalid compressed editor block')
        if len(data) != original_size:
            raise ValueError('Editor block length mismatch')
        blocks[name] = data
    if cursor != len(blob):
        raise ValueError('Unconsumed editor archive bytes')
    return root, blocks


def encode(root, blocks, compress=True):
    out = bytearray(encode_string(ET.tostring(root, encoding='utf-8')))
    out.extend(struct.pack('<I', len(blocks)))
    for name, raw in sorted(blocks.items()):
        stored = zlib.compress(raw) if compress and raw else raw
        compressed = len(stored) < len(raw)
        if not compressed:
            stored = raw
        out.extend(encode_string(name))
        out.extend(struct.pack('<III', len(stored) | (int(compressed) << 31), len(raw), 0))
        out.extend(stored)
    return bytes(out)
