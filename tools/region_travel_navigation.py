"""Package original-world navigation after checking the native binary contract.

This is format validation, not a claim that all quest-dependent navigation states
or companion spawning have been verified in the target runtime.
"""
import hashlib
from pathlib import Path
import struct
import zipfile

from upgrade_map import read


def validate_mesh(header, mesh, native_settings, native_version):
    if len(mesh) < 60 or mesh[:56] != native_settings:
        raise ValueError('Navigation settings differ from the installed target format')
    pos, cursor, tiles = 0, 60, 0
    while pos < len(header):
        if pos + 16 > len(header):
            raise ValueError('Truncated navigation index')
        x, y, offset, count = struct.unpack_from('<4I', header, pos)
        pos += 16
        if not count or pos + count * 4 > len(header) or offset != cursor:
            raise ValueError('Invalid navigation index offset or layer count')
        sizes = struct.unpack_from('<' + 'I' * count, header, pos)
        pos += count * 4
        for size in sizes:
            if size < 16 or cursor + size > len(mesh):
                raise ValueError('Navigation tile exceeds payload bounds')
            magic, version, tx, ty = struct.unpack_from('<4I', mesh, cursor)
            if (magic, version, tx, ty) != (0x444e4156, native_version, x, y):
                raise ValueError('Navigation tile format or coordinates differ')
            cursor += size
            tiles += 1
    if cursor != len(mesh) or tiles != struct.unpack_from('<I', mesh, 56)[0]:
        raise ValueError('Navigation tile count or payload extent differs')
    return tiles


def package_navigation(source, target, destination):
    """Keep source coordinates, indices and state variants; never use target geometry."""
    source = Path(source) / 'Data/Levels/rataje'
    target = Path(target) / 'Data/Levels/trosecko'
    with zipfile.ZipFile(target / 'recast.pak') as z:
        native = read(z, 'recast/rdnavmesh_1.nav')
    version = struct.unpack_from('<I', native, 64)[0]
    pairs, total, base_tiles = 0, 0, 0
    with zipfile.ZipFile(source / 'recast.pak') as src:
        # Read and validate before creating any output file.
        entries = {n: read(src, n) for n in src.namelist()}
        for name, header in entries.items():
            if not name.lower().endswith('.hdr'):
                continue
            mesh = entries[name[:-4] + '.nav']
            count = validate_mesh(header, mesh, native[:56], version)
            pairs += 1
            total += count
            if name.lower() == 'recast/rdnavmesh_1.hdr':
                base_tiles = count
    if not base_tiles:
        raise ValueError('Source has no base navigation mesh')
    with zipfile.ZipFile(source / 'level.pak') as src, zipfile.ZipFile(target / 'level.pak') as dst:
        graph = read(src, 'ubernav.tmm')
        native_graph = read(dst, 'ubernav.tmm')
        if graph[:4] != native_graph[:4]:
            raise ValueError('Source navigation graph version differs from target')
        level_entries = {n: read(src, n) for n in ('ubernav.tmm', 'areasmission0.bai')}
    output = Path(destination) / 'recast.pak'
    with zipfile.ZipFile(output, 'x', zipfile.ZIP_STORED) as dst:
        for name, blob in entries.items():
            dst.writestr(name, blob)
    return level_entries, {'source': str(source), 'base_tiles': base_tiles,
        'mesh_pairs': pairs, 'total_tiles_including_variants': total,
        'tile_version': version, 'graph_version': struct.unpack_from('<I', graph)[0],
        'source_archive_sha256': hashlib.sha256((source / 'recast.pak').read_bytes()).hexdigest(),
        'runtime_verified': False, 'quest_navigation_states_verified': False}
