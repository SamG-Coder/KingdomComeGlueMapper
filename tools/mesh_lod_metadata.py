"""Stable face-area metadata for imported compiled static meshes.

Use render subsets and CryEngine's 10e-6 minimum triangle area. Geometry,
materials, physics, node transforms and external LOD filenames are unchanged.
Do not recompile compiled CGFs through RC: its export path can erase this value.
"""
import struct
import numpy as np
from clothing_regions import read_chunks, write_chunks

HAS_FACE_AREA = 8
PREFIXES = ('gluebuild/', 'glueveg/', 'gluelandscape/', 'glueitems/', 'gluewater/')


def face_area(mesh, chunks, minimum_area=1e-5):
    if mesh.version != 0x801 or len(mesh.data) != 264:
        raise ValueError('Unsupported static mesh header')
    flags, _, vertices, indices, subsets, subset_id = struct.unpack_from('<6I', mesh.data)
    if flags & 1 or not indices:
        return None
    streams = struct.unpack_from('<16I', mesh.data, 28)

    def stream(kind):
        c = chunks[streams[kind]]
        if c.kind != 0x1016 or c.version != 0x800 or len(c.data) < 24:
            raise ValueError('Unsupported static mesh stream')
        _, typ, count, width = struct.unpack_from('<4I', c.data)
        if typ != kind or len(c.data) != 24 + count * width:
            raise ValueError('Invalid static mesh stream size/type')
        return c.data[24:], count, width

    kind = 0 if streams[0] else 15
    data, count, width = stream(kind)
    if count != vertices or (kind == 0 and width not in (8, 12)) or (kind == 15 and width != 16):
        raise ValueError('Unsupported position stream')
    dtype = '<f4' if width == 12 else '<f2'
    positions = np.ndarray((count, 3), dtype=dtype, buffer=data,
                           strides=(width, np.dtype(dtype).itemsize)).astype(np.float32)
    data, count, width = stream(5)
    if count != indices or width not in (2, 4):
        raise ValueError('Unsupported index stream')
    index = np.frombuffer(data, dtype='<u2' if width == 2 else '<u4')
    subset = chunks[subset_id]
    if subset.kind != 0x1017 or subset.version != 0x800 or len(subset.data) < 16 + subsets * 36:
        raise ValueError('Unsupported mesh subsets')
    if struct.unpack_from('<I', subset.data, 4)[0] != subsets:
        raise ValueError('Mismatched subset count')
    logs = []
    for i in range(subsets):
        first, count = struct.unpack_from('<2I', subset.data, 16 + i * 36)
        if count % 3 or first + count > indices:
            raise ValueError('Invalid subset index range')
        ids = index[first:first + count]
        if len(ids) and ids.max() >= vertices:
            raise ValueError('Invalid vertex index')
        triangles = positions[ids.reshape(-1, 3)]
        cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        areas = np.sqrt(np.sum(cross * cross, axis=1)) * np.float32(0.5)
        if not np.isfinite(areas).all():
            raise ValueError('Non-finite triangle area')
        logs.extend(np.log(areas[(areas > 0) & (areas >= np.float32(minimum_area))]))
    if not logs:
        return None  # Degenerate/tiny-only meshes have no engine area statistic.
    # Sequential float accumulation matches the engine-family implementation.
    total = np.cumsum(np.asarray(logs, dtype=np.float32), dtype=np.float32)[-1]
    return float(np.exp(np.float32(total / len(logs))))


def normalize(blob):
    chunks = read_chunks(blob)
    by_id = {c.id: c for c in chunks}
    replacements = {}
    for mesh in chunks:
        if mesh.kind != 0x1000:
            continue
        # Native metadata is authoritative; never alter native/previously fixed assets.
        if struct.unpack_from('<I', mesh.data)[0] & HAS_FACE_AREA:
            continue
        area = face_area(mesh, by_id)
        if area is None:
            continue
        data = bytearray(mesh.data)
        struct.pack_into('<I', data, 0, struct.unpack_from('<I', data)[0] | HAS_FACE_AREA)
        struct.pack_into('<f', data, 136, area)
        replacements[mesh.id] = bytes(data)
    return write_chunks(chunks, replacements) if replacements else blob


def generated_entries(entries):
    for name, payload in entries:
        lower = name.replace('\\', '/').lower()
        if lower.startswith(PREFIXES) and lower.endswith('.cgf'):
            payload = normalize(payload)
        yield name, payload


def upgrade_package(package):
    """Rewrite only changed shards, one at a time, on the package's own drive."""
    from pathlib import Path
    import zipfile
    from setup_campaign import digest
    from setup_progress import progress
    changed = {}
    count = 0
    paths = sorted((Path(package) / 'Data').glob('kingdomcomegluemapper_world_*.pak'))
    for i, path in enumerate(paths):
        replacements = {}
        with zipfile.ZipFile(path) as source:
            for entry in source.infolist():
                name = entry.filename.replace('\\', '/').lower()
                if name.startswith(PREFIXES) and name.endswith('.cgf'):
                    old = source.read(entry)
                    new = normalize(old)
                    if new != old:
                        replacements[entry.filename] = new
            if replacements:
                pending = path.with_suffix('.lod-pending')
                try:
                    with zipfile.ZipFile(pending, 'x', zipfile.ZIP_STORED, allowZip64=False) as out:
                        for entry in source.infolist():
                            out.writestr(entry, replacements[entry.filename] if entry.filename in replacements else source.read(entry))
                    with zipfile.ZipFile(pending) as check:
                        if check.testzip() is not None:
                            raise ValueError('Mesh metadata migration verification failed')
                except Exception:
                    if pending.exists():
                        pending.unlink()
                    raise
        if replacements:
            pending.replace(path)
            changed[path.relative_to(package).as_posix()] = digest(path)
            count += len(replacements)
        progress('Updating static mesh LOD metadata', i + 1, len(paths), path.name)
    return changed, dict(version=1, updated_mesh_files=count, runtime_verified=False)
