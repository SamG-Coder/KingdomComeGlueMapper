"""Bounded readers for the two observed retail terrain formats.

Reference record shapes: Crytek terrain_compile.cpp, terrain_node_compile.cpp,
terrain_sector.h. Every section boundary is verified against installed files.
"""
from array import array
from dataclasses import dataclass
import math
import struct
import sys


@dataclass
class Terrain:
    data: bytes
    version: int
    unit: float
    tables: dict
    nodes: list
    tree_offset: int
    tree_nodes: int


def parse(data):
    if sys.byteorder != "little":
        raise ValueError("Only little-endian hosts supported")
    if len(data) < 44:
        raise ValueError("Truncated terrain")
    version, dummy, flags, flags2, length = struct.unpack_from("<4BI", data)
    if version not in (28, 29) or flags != 6 or length != len(data):
        raise ValueError("Unknown terrain header or size mismatch")
    grid = struct.unpack_from("<I", data, 8)[0]
    unit = struct.unpack_from("<i" if version == 28 else "<f", data, 12)[0]
    sector, sectors = struct.unpack_from("<II", data, 16)
    if (grid, unit, sector, sectors) != (4096, 1, 64, 64):
        raise ValueError("This prototype supports only the observed 4096/1/64/64 layout")
    offset = 32
    tables = {}
    for name, stride in (("vegetation", 360), ("meshes", 256), ("materials", 256)):
        count = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        if count > 1000000 or offset + count * stride > len(data):
            raise ValueError("Invalid resource table bounds")
        paths = [data[offset+i*stride:offset+i*stride+256].split(b"\0", 1)[0].decode("utf-8") for i in range(count)]
        tables[name] = {"offset": offset, "stride": stride, "paths": paths}
        offset += count * stride
    nodes = []
    item_size = 2 if version == 28 else 4

    def node(depth):
        nonlocal offset
        start = offset
        values = struct.unpack_from("<hh8fii", data, offset)
        ver, holes = values[:2]
        size, surfaces = values[-2:]
        if ver != (7 if version == 28 else 8) or holes not in (0, 1, 2) or size not in (0, 2, 3, 5, 9, 17, 33, 65) or not 0 <= surfaces <= 127:
            raise ValueError(f"Unexpected terrain node at {start}: {values}")
        offset += 44
        samples = offset
        offset = (offset + size*size*item_size + 3) & ~3
        error_offset = offset
        offset += 24
        palette_offset = offset
        offset = (offset + surfaces + 3) & ~3
        if offset > len(data):
            raise ValueError("Terrain node exceeds data")
        nodes.append({"offset": start, "end": offset, "depth": depth, "size": size,
                      "samples": samples, "errors": error_offset, "palette": palette_offset,
                      "surface_count": surfaces, "values": values})
        if depth < 6:
            for _ in range(4):
                node(depth+1)
    node(0)
    tree_offset = offset
    tree_nodes = 0

    def tree(depth=0):
        nonlocal offset, tree_nodes
        if depth > 32 or offset+32 > len(data):
            raise ValueError("Invalid object tree bounds")
        ver, mask, dummy, *rest = struct.unpack_from("<HBB6fI", data, offset)
        if ver != (5 if version == 28 else 8):
            raise ValueError(f"Unknown object tree version {ver} at {offset}")
        offset += 32 + rest[-1]
        if offset > len(data):
            raise ValueError("Object payload exceeds file")
        tree_nodes += 1
        for bit in range(8):
            if mask & (1 << bit):
                tree(depth+1)
    tree()
    if offset != len(data) or len(nodes) != 5461:
        raise ValueError("Unconsumed bytes or incomplete terrain tree")
    return Terrain(data, version, unit, tables, nodes, tree_offset, tree_nodes)


def convert_elevation(source):
    """Convert terrain nodes; omit unconverted outdoor objects explicitly.

    v7 packs surface in low 4 bits and elevation in high 12 bits of uint16.
    v8 packs 20 surface bits and 12 elevation bits in uint32. The installed
    source samples already use a fixed 0.05 metre step despite the legacy
    fRange header value. Preserve their quantized heights and offsets exactly.
    """
    if source.version != 28:
        raise ValueError("Expected version 28 input")
    output = bytearray(source.data[:32])
    output[0] = 29
    output[3] = 0  # no layer activation in this terrain-only probe
    struct.pack_into("<f", output, 12, source.unit)
    output.extend(bytes(12))  # explicitly empty resource tables
    sample_count = 0
    for node in source.nodes:
        header = bytearray(source.data[node["offset"]:node["offset"]+44])
        struct.pack_into("<h", header, 0, 8)
        old_offset, _legacy_range = struct.unpack_from("<ff", header, 28)
        count = node["size"]**2
        old = array("H")
        old.frombytes(source.data[node["samples"]:node["samples"]+2*count])
        step = struct.unpack("<f", struct.pack("<f", 0.05))[0]
        new_offset = old_offset
        if count:
            struct.pack_into("<ff", header, 28, new_offset, step)
        output.extend(header)
        quantized = [v >> 4 for v in old]
        if any(q < 0 or q > 4095 for q in quantized):
            raise ValueError("Sector elevation exceeds target quantization range")
        converted = array("I", ((q << 20) | (v & 15) for q,v in zip(quantized,old)))
        output.extend(converted.tobytes())
        sample_count += count
        output.extend(bytes((-len(output)) % 4))
        # The newer loader reads only slot zero as m_geomError; the old
        # per-LOD array starts with zero. Use a conservative source error
        # for internal nodes until target-style errors are recomputed.
        # Native target leaves have no simplification error.
        errors = struct.unpack_from("<6f", source.data, node["errors"])
        if any(not math.isfinite(e) or e < 0 for e in errors):
            raise ValueError("Invalid source geometry error")
        geom_error = max(errors) if node["depth"] < 6 else 0.0
        output.extend(struct.pack("<6f", geom_error, 0, 0, 0, 0, 0))
        output.extend(source.data[node["palette"]:node["palette"]+node["surface_count"]])
        output.extend(bytes((-len(output)) % 4))
    output.extend(struct.pack("<HBB6fI", 8, 0, 0, 0, 0, 0, 4096, 4096, 4096, 0))
    struct.pack_into("<I", output, 4, len(output))
    result = parse(bytes(output))
    # Check every converted sample, not just counts or file size.
    for before, after in zip(source.nodes, result.nodes):
        old, new = array("H"), array("I")
        n = before["size"]**2
        old.frombytes(source.data[before["samples"]:before["samples"]+2*n])
        new.frombytes(result.data[after["samples"]:after["samples"]+4*n])
        if before["values"][2:8] != after["values"][2:8]:
            raise ValueError("Terrain bounds changed")
        offset1, _legacy_range = before["values"][8:10]
        offset2, scale2 = after["values"][8:10]
        if any(abs(offset1+(a >> 4)*0.05-offset2-(b >> 20)*scale2) > 0.00001 or (b & 0xfffff) != (a & 15) for a,b in zip(old,new)):
            raise ValueError("Height or surface sample mismatch")
    return result, sample_count
