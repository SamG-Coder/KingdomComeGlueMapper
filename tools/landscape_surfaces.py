"""Convert observed KCD1 road/decal records without changing their placement."""
import math
import struct


def expand_tangents(payload):
    """Decode the signed XY/LSB format used by KCD1 Common.cfi.

    The low X bit stores Z's sign; tangent Y stores handedness and binormal
    Y enables reconstructed Z. KCD2 roads store two signed-short XYZ/W vectors.
    This is not the quaternion format used by some vegetation meshes.
    """
    if len(payload) % 8:
        raise ValueError("Truncated compressed road tangent")
    output = bytearray()
    for tx, ty, bx, by in struct.iter_unpack('<4h', payload):
        handedness = -32767 if ty & 1 else 32767
        vectors = []
        for x, y in ((tx, ty), (bx, by)):
            x0, y0 = x & ~1, y & ~1
            z = math.sqrt(max(0.0, 32767.0**2 - x0*x0 - y0*y0))
            z *= (-1 if x & 1 else 1) * (by & 1)
            vectors.extend((max(-32767, x0), max(-32767, y0), round(z), handedness))
        output.extend(struct.pack('<8h', *vectors))
    return bytes(output)


def convert_surface(record, material):
    kind = struct.unpack_from('<I', record)[0]
    if kind == 7:
        if len(record) != 116:
            raise ValueError("Unexpected source decal size")
        output = bytearray(record[:36] + bytes(4) + record[36:])
        struct.pack_into('<i', output, 112, material)
    elif kind == 11:
        if len(record) < 108:
            raise ValueError("Truncated road header")
        v, i, t, p, s = struct.unpack_from('<5I', record, 80)
        end = 108 + v*12 + i*2 + t*8 + p*40 + s*12
        if (end + 3) & ~3 != len(record) or t != v:
            raise ValueError("Unexpected source road layout")
        indices_at = 108 + v*12
        if any(x[0] >= v for x in struct.iter_unpack('<H', record[indices_at:indices_at+i*2])):
            raise ValueError("Road index exceeds vertex buffer")
        tangents_at = indices_at + i*2
        output = bytearray(record[:36] + bytes(4) + record[36:tangents_at])
        # Native KCD2 road tangent arrays begin on a four-byte boundary.
        # Odd index counts leave a two-byte gap (0xDEDE in retail captures).
        # KCD1's compressed tangent stream has no such gap. Padding only the
        # record's end shifts every native tangent by one signed short.
        output.extend(bytes(-len(output) % 4))
        output.extend(expand_tangents(record[tangents_at:tangents_at+t*8]))
        output.extend(record[tangents_at+t*8:end])
        output.extend(bytes(-len(output) % 4))
        struct.pack_into('<i', output, 108, material)
    else:
        raise ValueError("Expected road or decal")
    struct.pack_into('<H', output, 28, 0)  # active development layer
    return bytes(output)


def append_surfaces(terrain, records, materials):
    """Extend the material table and root payload, preserving all existing IDs."""
    if terrain.version != 29:
        raise ValueError("Expected target terrain")
    table = terrain.tables['materials']
    count = len(table['paths'])
    table_end = table['offset'] + count*256
    prefix = bytearray(terrain.data[:table_end])
    struct.pack_into('<I', prefix, table['offset']-4, count+len(materials))
    for name in materials:
        encoded = name.encode('utf-8')
        if not encoded or len(encoded) >= 256:
            raise ValueError("Invalid material path")
        prefix.extend(encoded.ljust(256, b'\0'))
    prefix.extend(terrain.data[table_end:terrain.tree_offset])
    root = bytearray(terrain.data[terrain.tree_offset:terrain.tree_offset+32])
    size = struct.unpack_from('<I', root, 28)[0]
    payload = b''.join(records)
    struct.pack_into('<I', root, 28, size+len(payload))
    tree = terrain.data[terrain.tree_offset+32:]
    output = prefix + root + tree[:size] + payload + tree[size:]
    struct.pack_into('<I', output, 4, len(output))
    return bytes(output)
