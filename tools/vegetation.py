"""Bounded readers for observed KCD1 vegetation and KCD2 HLOD records.

Only leading vegetation records in each KCD1 object block are decoded.
Unknown record types terminate that block's inspection, never a guessed stride.
"""
import math
import struct
from collections import Counter


def read_instances(terrain):
    data = terrain.data
    cursor = terrain.tree_offset
    instances = []
    skipped = Counter()

    def visit(depth=0):
        nonlocal cursor
        if depth > 32 or cursor + 32 > len(data):
            raise ValueError("Invalid vegetation octree")
        header = struct.unpack_from("<HBB6fI", data, cursor)
        if header[0] != 5:
            raise ValueError("Expected source octree version 5")
        cursor += 32
        end = cursor + header[-1]
        if end > len(data):
            raise ValueError("Object block out of bounds")
        while cursor < end:
            kind = struct.unpack_from("<I", data, cursor)[0]
            if kind != 2:
                skipped[kind] += 1
                cursor = end
                break
            if cursor + 64 > end:
                raise ValueError("Truncated vegetation record")
            group = struct.unpack_from("<H", data, cursor+36)[0]
            x, y, z, scale = struct.unpack_from("<4f", data, cursor+40)
            if group >= len(terrain.tables["vegetation"]["paths"]) or not all(map(math.isfinite, (x,y,z,scale))) or not 0 < scale < 100:
                raise ValueError("Invalid vegetation transform or group")
            instances.append({"offset": cursor, "group": group,
                              "mesh": terrain.tables["vegetation"]["paths"][group],
                              "pos": [x,y,z], "scale": scale})
            cursor += 64
        for bit in range(8):
            if header[1] & (1 << bit):
                visit(depth+1)
    visit()
    if cursor != len(data):
        raise ValueError("Unconsumed source object tree")
    return instances, dict(skipped)


def read_all_instances(terrain):
    """Read instances anywhere in mixed blocks, using the complete bounded walker."""
    from water_volumes import read_water
    if terrain.version != 28:
        raise ValueError("Expected source vegetation version 28")
    instances = []
    def collect(data, offset, size, kind):
        if kind != 2:
            return
        group = struct.unpack_from("<H", data, offset + 36)[0]
        values = struct.unpack_from("<4f", data, offset + 40)
        if group >= len(terrain.tables["vegetation"]["paths"]) or not all(map(math.isfinite, values)) or not 0 < values[3] < 100:
            raise ValueError("Invalid vegetation transform or group")
        instances.append({"offset": offset, "group": group,
                          "mesh": terrain.tables["vegetation"]["paths"][group],
                          "pos": list(values[:3]), "scale": values[3],
                          "layer": struct.unpack_from("<H", data, offset + 28)[0]})
    _, counts = read_water(terrain, collect)
    return instances, counts


def verify_target_hlods(data):
    if struct.unpack_from("<I", data)[0] != 2:
        raise ValueError("Unknown HLOD container")
    cursor = 4
    counts = Counter()
    while cursor < len(data):
        size = struct.unpack_from("<I", data, cursor)[0]
        cursor += 4
        end = cursor + size
        if end > len(data):
            raise ValueError("HLOD block exceeds file")
        while cursor < end:
            kind = struct.unpack_from("<I", data, cursor)[0]
            if kind not in (1, 2):
                raise ValueError("Unknown native HLOD record")
            counts[kind] += 1
            cursor += 104 if kind == 1 else 64
        if cursor != end:
            raise ValueError("HLOD record exceeds block")
    return dict(counts)


def convert_instance(data, offset, group):
    # Source: 32-bit render flags, transform, angles, trailing opaque word.
    # Target: 64-bit render flags, transform and angles; no trailing word.
    original = data[offset:offset+64]
    if len(original) != 64 or struct.unpack_from("<I", original)[0] != 2:
        raise ValueError("Invalid source vegetation record")
    converted = bytearray(original[:36] + bytes(4) + original[36:60])
    struct.pack_into("<H", converted, 28, 0)  # independent test layer
    struct.pack_into("<H", converted, 40, group)
    if converted[44:64] != original[40:60]:
        raise ValueError("Vegetation transform changed")
    return bytes(converted)
