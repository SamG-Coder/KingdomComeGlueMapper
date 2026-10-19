"""Read authored OCM geometry and convert KCD1 placements to KCD2's layout.

This is an offline format tool, not an automatic installation stage. Occluders
must still agree with the converted world's active geometry: stale occluders
can hide valid surfaces. An OCM is not HLOD or a precomputed visibility grid.
"""
import argparse
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import struct
import zipfile

KCD1 = b'\xff\xff\xff\xfa'
KCD2 = b'\xff\xff\xff\xfb'


@dataclass(frozen=True)
class Instance:
    mesh_offset: int
    transform: bytes  # 3 x 4 little-endian float matrix, retained verbatim
    extents: bytes = b''  # KCD1-only cached bounds; not part of KCD2 instances


@dataclass(frozen=True)
class OcclusionMesh:
    version: bytes
    declared_mesh_count: int
    geometry: bytes  # includes the 16-byte file header; mesh offsets are absolute
    instances: tuple[Instance, ...]
    mesh_vertices: dict[int, int]


def parse(data):
    if len(data) < 16 or data[:4] not in (KCD1, KCD2):
        raise ValueError('Unsupported OCM version or truncated header')
    version = data[:4]
    declared, count, start = struct.unpack_from('<3I', data, 4)
    stride = 64 if version == KCD1 else 52
    if start < 16 or start % 16 or start + count * stride != len(data):
        raise ValueError('Invalid OCM geometry/instance extent')
    meshes = {}
    offset = 16
    while offset < start:
        vertices = struct.unpack_from('<I', data, offset)[0]
        end = offset + 16 + vertices * 16
        if vertices % 3 or end > start or any(data[offset + 4:offset + 16]):
            raise ValueError('Invalid OCM triangle block')
        for x, y, z, w in struct.iter_unpack('<4f', data[offset + 16:end]):
            if not all(map(math.isfinite, (x, y, z))) or w != 1.0:
                raise ValueError('Invalid OCM homogeneous vertex')
        meshes[offset] = vertices
        offset = end
    instances = []
    for index in range(count):
        pos = start + index * stride
        if version == KCD1:
            matrix = data[pos:pos + 48]
            extents = data[pos + 48:pos + 60]
            mesh = struct.unpack_from('<I', data, pos + 60)[0]
            if any(not math.isfinite(v) or v < 0 for v in struct.unpack('<3f', extents)):
                raise ValueError('Invalid OCM instance extents')
        else:
            mesh = struct.unpack_from('<I', data, pos)[0]
            matrix = data[pos + 4:pos + 52]
            extents = b''
        if mesh not in meshes:
            raise ValueError('OCM instance references missing mesh')
        if not all(map(math.isfinite, struct.unpack('<12f', matrix))):
            raise ValueError('Invalid OCM transform')
        instances.append(Instance(mesh, matrix, extents))
    # Native Trosky reports 222 meshes but stores 117 distinct triangle blocks.
    # The declared count is not a unique-block count; do not walk by that field.
    return OcclusionMesh(version, declared, data[:start], tuple(instances), meshes)


def to_kcd2(data):
    source = parse(data)
    if source.version == KCD2:
        return data
    result = bytearray(source.geometry)
    result[:4] = KCD2
    for instance in source.instances:
        result.extend(struct.pack('<I', instance.mesh_offset))
        result.extend(instance.transform)
    result = bytes(result)
    target = parse(result)
    if target.geometry[4:] != source.geometry[4:] or any(
            (a.mesh_offset, a.transform) != (b.mesh_offset, b.transform)
            for a, b in zip(source.instances, target.instances)):
        raise ValueError('OCM conversion changed geometry or placement')
    return result


def describe(data):
    mesh = parse(data)
    return dict(format='KCD1' if mesh.version == KCD1 else 'KCD2',
                bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                declared_mesh_count=mesh.declared_mesh_count,
                unique_mesh_blocks=len(mesh.mesh_vertices),
                triangles=sum(mesh.mesh_vertices.values()) // 3,
                instances=len(mesh.instances),
                placed_triangles=sum(mesh.mesh_vertices[i.mesh_offset]
                                     for i in mesh.instances) // 3)


def read_input(path):
    if path.suffix.lower() == '.pak':
        from upgrade_map import read
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist()
                     if n.replace('\\', '/').lower() == 'occluder.ocm']
            if len(names) != 1:
                raise ValueError('Expected one root occluder.ocm in level archive')
            return read(archive, names[0])
    return path.read_bytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='OCM file or level.pak')
    parser.add_argument('--output', type=Path, help='New standalone KCD2 OCM candidate; never installs')
    args = parser.parse_args()
    source = read_input(args.source)
    report = {'source': describe(source)}
    if args.output:
        if args.output.suffix.lower() != '.ocm':
            parser.error('Output must be a standalone .ocm file')
        converted = to_kcd2(source)
        with args.output.open('xb') as stream:
            stream.write(converted)
        report.update(target=describe(converted), runtime_verified=False,
                      world_coverage_verified=False, installed=False)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
