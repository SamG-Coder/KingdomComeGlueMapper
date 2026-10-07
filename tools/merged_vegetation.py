"""Convert observed KCD1 merged sectors to an isolated KCD2 test layout.

The compact twelve-byte transforms are retained byte for byte. Both legacy
sector parts are combined, with matching descriptor and stream group IDs.
Geometry IDs are local deterministic identifiers; runtime compatibility must
be checked independently of these structural checks.
"""
from collections import defaultdict
import re
import struct

from upgrade_map import read


MAGIC = 0xCAFEBAB6
SECTOR = re.compile(r"terrain/merged_meshes_sectors/sector_(\d+)_(\d+)_(\d+)_(\d+)\.dat$")


def decode_sector(data, expected_cell=None, group_limit=None):
    result = []
    offset = 0
    while offset < len(data):
        if offset + 24 > len(data):
            raise ValueError("Truncated merged sector header")
        magic, x, y, z, group, count = struct.unpack_from("<6I", data, offset)
        end = offset + 24 + count * 12
        if magic != MAGIC or not count or end > len(data):
            raise ValueError("Invalid merged sector header or sample bounds")
        if expected_cell is not None and (x, y, z) != expected_cell:
            raise ValueError("Merged sector coordinates disagree with filename")
        if group_limit is not None and group >= group_limit:
            raise ValueError("Merged sector references an invalid source group")
        result.append((group, data[offset + 24:end]))
        offset = end
    if not result:
        raise ValueError("Empty merged sector")
    return result


def inventory(archive, group_limit):
    cells = defaultdict(list)
    groups = set()
    instances = 0
    for entry in archive.infolist():
        match = SECTOR.fullmatch(entry.filename.replace("\\", "/").lower())
        if not match:
            continue
        x, y, z, part = map(int, match.groups())
        if x >= 256 or y >= 256 or z >= 256 or part not in (0, 1):
            raise ValueError("Unsupported merged sector addressing")
        samples = decode_sector(read(archive, entry.filename), (x, y, z), group_limit)
        cells[(x, y, z)].append((part, entry.filename))
        for group, payload in samples:
            groups.add(group)
            instances += len(payload) // 12
    if not cells:
        raise ValueError("No merged sector files found")
    return dict(cells), groups, instances


def convert_cell(archive, cell, entries, group_map):
    combined = defaultdict(bytearray)
    for _, name in sorted(entries):
        for group, payload in decode_sector(read(archive, name), cell):
            combined[group].extend(payload)
    stream = bytearray()
    descriptors = bytearray()
    for group, payload in sorted(combined.items()):
        mapped = group_map[group]
        geometry_id = 0x47000000 + mapped
        count = len(payload) // 12
        stream.extend(struct.pack("<6I", MAGIC, *cell, geometry_id, count))
        stream.extend(payload)
        descriptors.extend(struct.pack("<III", mapped, count, geometry_id))
    # Conservative visible bounds include models extending outside their cell.
    low = tuple(v * 16.0 for v in cell)
    high = tuple(v + 16.0 for v in low)
    bbox = tuple(v - 16.0 for v in low) + tuple(v + 16.0 for v in high)
    record = struct.pack("<I6fHbbQHBBI6f", 23, *bbox, 0, 0, 0,
                         0x40008808, 0, 255, 100, len(combined), *low, *high)
    return record + descriptors, bytes(stream)


def build_tree(records):
    """Spatial octree with 64m leaves; empty branches are not serialized."""
    def visit(items, origin, size, depth):
        children = defaultdict(list)
        payload = b""
        if depth == 6:
            payload = b"".join(record for _, record in items)
        else:
            half = size / 2
            for cell, record in items:
                # Native child order: X is bit 2, Y bit 1, Z bit 0.
                index = sum((1 << (2 - axis)) for axis in range(3)
                            if cell[axis] * 16 + 8 >= origin[axis] + half)
                children[index].append((cell, record))
        mask = sum(1 << i for i in children)
        header = struct.pack("<HBB6fI", 8, mask, 0, *origin,
                             *(v + size for v in origin), len(payload))
        output = bytearray(header + payload)
        for index, child in sorted(children.items()):
            next_origin = tuple(origin[a] + (size / 2 if index & (1 << (2 - a)) else 0)
                                for a in range(3))
            output.extend(visit(child, next_origin, size / 2, depth + 1))
        return output
    return visit(list(records.items()), (0.0, 0.0, 0.0), 4096.0, 0)
