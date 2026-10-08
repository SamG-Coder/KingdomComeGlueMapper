"""Convert retail trigger polygons to the native KCD2 version-3 package.

The source package contains WORLD-space vertices. Do not apply the entity's
transform again. KCD1's runtime WUIDs/counter are not persistent KCD2 identities;
the original 64-bit entity GUID is the binding key on both sides.
"""
from dataclasses import dataclass
import math
import struct


@dataclass(frozen=True)
class TriggerArea:
    guid: int
    height: float
    points: tuple
    source_wuid: int | None = None


def read_areas(data):
    """Read v1/v3 exactly; reject truncated/unknown data rather than guessing."""
    if len(data) < 12:
        raise ValueError('Truncated trigger-area header')
    version = struct.unpack_from('<I', data)[0]
    if version == 1:
        if len(data) < 16:
            raise ValueError('Truncated version-1 trigger-area header')
        source_counter, count = struct.unpack_from('<QI', data, 4)
        offset, export_version = 16, None
    elif version == 3:
        export_version, count = struct.unpack_from('<II', data, 4)
        offset, source_counter = 12, None
    else:
        raise ValueError(f'Unsupported trigger-area version {version}')
    areas, seen = [], set()
    for _ in range(count):
        prefix = 24 if version == 1 else 16
        if offset + prefix > len(data):
            raise ValueError('Truncated trigger-area record')
        wuid = struct.unpack_from('<Q', data, offset)[0] if version == 1 else None
        guid, height, n = struct.unpack_from('<QfI', data, offset + (8 if version == 1 else 0))
        offset += prefix
        if not guid or guid in seen:
            raise ValueError('Zero or duplicate trigger-area GUID')
        if not math.isfinite(height) or height < 0:
            raise ValueError('Invalid trigger-area height')
        # Shipped KCD2 data includes two-point areas; preserve them verbatim.
        if n < 2 or offset + n * 12 > len(data):
            raise ValueError('Invalid or truncated trigger-area points')
        points = tuple(struct.unpack_from('<fff', data, offset + i * 12) for i in range(n))
        if not all(math.isfinite(v) for p in points for v in p):
            raise ValueError('Non-finite trigger-area coordinate')
        offset += n * 12
        seen.add(guid)
        areas.append(TriggerArea(guid, height, points, wuid))
    if offset != len(data):
        raise ValueError('Unrecognized trailing trigger-area data')
    return dict(version=version, export_version=export_version,
                source_counter=source_counter, areas=areas)


def write_areas(areas, export_version):
    if not isinstance(export_version, int) or not 0 < export_version <= 0xffffffff:
        raise ValueError('A nonzero level export version is required')
    areas = list(areas)
    output = bytearray(struct.pack('<III', 3, export_version, len(areas)))
    for area in areas:
        output.extend(struct.pack('<QfI', area.guid, area.height, len(area.points)))
        for point in area.points:
            output.extend(struct.pack('<fff', *point))
    # Use the same validation for callers constructing records independently.
    read_areas(output)
    return bytes(output)


def convert_areas(data, export_version, guids=None):
    source = read_areas(data)
    if source['version'] != 1:
        raise ValueError('Expected a retail KCD1 version-1 package')
    selected = source['areas']
    if guids is not None:
        guids = set(guids)
        missing = guids - {a.guid for a in selected}
        if missing:
            raise ValueError('Trigger geometry missing for GUIDs: ' + ', '.join(f'{n:016x}' for n in sorted(missing)))
        selected = [a for a in selected if a.guid in guids]
    return write_areas(selected, export_version), dict(
        source_version=1, target_version=3, export_version=export_version,
        source_areas=len(source['areas']), converted_areas=len(selected),
        vertices=sum(len(a.points) for a in selected),
        coordinate_space='world', identities='original 64-bit entity GUIDs')
